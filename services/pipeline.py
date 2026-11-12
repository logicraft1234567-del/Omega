"""Evidence intelligence pipeline: frames -> per-video reading -> comparison -> risk -> outcome.
Runs in a worker thread (swap for Celery/Redis in production; the function signature stays the same)."""
import os, traceback
from concurrent.futures import ThreadPoolExecutor
from flask import current_app
from models import db, Evidence, VerificationResult, Transaction, Dispute, audit, notify
from . import ai, risk, storage, video as V, policy

_pool = ThreadPoolExecutor(max_workers=2)


def submit(evidence_id):
    app = current_app._get_current_object()
    ev = db.session.get(Evidence, evidence_id)
    ev.analysis_status = "queued"; db.session.commit()
    if app.config.get("SYNC_ANALYSIS"):
        _run(app, evidence_id)
    else:
        _pool.submit(_run, app, evidence_id)


def _run(app, evidence_id):
    with app.app_context():
        ev = db.session.get(Evidence, evidence_id)
        try:
            ev.analysis_status = "running"; db.session.commit()
            process_video(ev)
            if ev.analysis_status == "running":      # process_video sets "unavailable" itself when an AI engine can't be reached
                ev.analysis_status = "done"
            db.session.commit()
        except Exception:
            db.session.rollback()
            ev = db.session.get(Evidence, evidence_id)
            ev.analysis_status = "failed"
            ev.meta = (ev.meta or {}) | {"error": traceback.format_exc()[-500:]}
            tx = ev.transaction
            tx.log("system", "Automatic analysis failed — routed to a human reviewer", "system")
            if tx.can_go("MANUAL_REVIEW"):
                tx.transition("MANUAL_REVIEW", "system", "analysis error")
            db.session.commit()


def _listing_text(tx):
    s = tx.product.spec_snapshot()
    return ", ".join(f"{k}: {v}" for k, v in s.items() if v)


def _engine_line(items):
    return "; ".join(f"{i['name']} ({i['provider']}): {i['reason']}" for i in items)


def _ai_down(tx, ev, items, what):
    """An AI engine couldn't be reached. Nothing is guessed or faked: record the reason, tell people, wait for a retry."""
    ev.analysis_status = "unavailable"
    ev.meta = (ev.meta or {}) | {"ai_error": items}
    line = _engine_line(items)
    tx.log("ai", f"{what} could not be completed — {line}", "omega-ai", {"unavailable": items})
    mine = tx.seller_id if ev.kind == "seller_video" else tx.buyer_id
    notify(mine, "verification", f"{tx.ref}: our AI couldn't check your video yet — {line}. Nothing is wrong with your video; use “Retry analysis”.", f"/orders/{tx.ref}")
    from models import User
    for a in User.query.filter_by(role="admin"):
        notify(a.id, "system", f"{tx.ref}: AI unavailable — {line}", f"/orders/{tx.ref}")
    db.session.commit()


def process_video(ev):
    tx = ev.transaction
    path = storage.abs_path(ev.path)
    meta = V.video_metadata(path)
    frames_dir = os.path.join(current_app.config["UPLOAD_ROOT"], "frames", str(ev.id))
    frames = V.extract_frames(path, frames_dir, current_app.config["FRAMES_PER_VIDEO"])
    ev.meta = {**meta, "frames": [os.path.relpath(f, current_app.config["UPLOAD_ROOT"]) for f in frames]}
    role = "seller" if ev.kind == "seller_video" else "buyer"
    try:
        reading = ai.read_video(path, _listing_text(tx), role, tx.challenge if role == "seller" else None)
    except ai.AIUnavailable as e:
        return _ai_down(tx, ev, [e.as_dict()], f"Reading the {role} video")
    ev.meta = {k: v for k, v in ev.meta.items() if k != "ai_error"}
    db.session.add(VerificationResult(transaction_id=tx.id, kind=ev.kind, engine=reading["engine"],
                                      recommendation="READABLE" if reading["product_visible"] else "UNREADABLE",
                                      confidence=reading["confidence"], report={"reading": reading, "metadata": meta}))
    db.session.flush()
    if ev.kind == "seller_video":
        _after_seller_video(tx, reading, meta)
    else:
        _after_buyer_video(tx, ev)
    r = risk.score_transaction(tx)
    tx.risk_score, tx.risk_reasons = r["score"], r["reasons"]
    db.session.commit()


def retry(tx, actor):
    """Re-run whatever stopped because an AI engine was unreachable. Returns a short description, or None if nothing to retry."""
    for kind in ("seller_video", "buyer_video"):
        ev = tx.latest_evidence(kind)
        if ev and ev.analysis_status in ("unavailable", "failed"):
            tx.log("ai", f"Analysis retried by {actor}", actor)
            db.session.commit(); submit(ev.id)
            return f"{kind.replace('_', ' ')} analysis"
    cmp_ = tx.latest_result("comparison")
    bev = tx.latest_evidence("buyer_video")
    if cmp_ and cmp_.recommendation == "AI_UNAVAILABLE" and bev and tx.status == "AI_VERIFICATION":
        tx.log("ai", f"Comparison retried by {actor}", actor)
        db.session.commit(); submit(bev.id)
        return "video comparison"
    return None


def _after_seller_video(tx, reading, meta):
    problems = []
    if not reading["product_visible"]:
        problems.append("The device isn't visible in the footage")
    if reading.get("challenge_satisfied") is False:
        problems.append(f"The random challenge wasn't shown: “{tx.challenge}”")
    if reading["confidence"] < 0.5:
        problems.append("Video quality is too low to inspect the device")
    p = tx.product
    if reading["model"] and p.model and p.model.lower() not in reading["model"].lower() and reading["model"].lower() not in p.model.lower():
        problems.append(f"Video shows “{reading['model']}” but the listing says “{p.model}”")
    if reading["colour"] and p.colour and p.colour.split()[0].lower() not in reading["colour"].lower():
        problems.append(f"Video colour “{reading['colour']}” differs from listing “{p.colour}”")
    if problems:
        tx.needs_evidence_from = "seller"
        tx.log("ai", "Seller video needs attention: " + "; ".join(problems), "omega-ai", {"problems": problems})
        tx.transition("NEEDS_MORE_EVIDENCE", "omega-ai", "seller video rejected")
        notify(tx.seller_id, "verification", f"Order {tx.ref}: please re-record your verification video.", f"/orders/{tx.ref}")
        return
    tx.log("ai", "Seller verification video accepted", "omega-ai", {"engine": "fire"})
    notify(tx.seller_id, "verification", f"Order {tx.ref}: verification video accepted.", f"/orders/{tx.ref}")
    advance_if_ready_to_ship(tx)


def advance_if_ready_to_ship(tx):
    """Seller video accepted AND both parties acknowledged the agreement."""
    if tx.status == "SELLER_VERIFICATION" and tx.latest_result("seller_video") and tx.agreement.acked_by("buyer") and tx.agreement.acked_by("seller"):
        sv = tx.latest_result("seller_video")
        if sv.recommendation == "READABLE":
            tx.transition("READY_TO_SHIP", "system", "evidence + agreement complete")
            notify(tx.seller_id, "shipping", f"Order {tx.ref} is ready to ship. Add tracking details.", f"/orders/{tx.ref}")


def _after_buyer_video(tx, ev):
    sv_ev, sv_res = tx.latest_evidence("seller_video"), tx.latest_result("seller_video")
    bv_res = tx.latest_result("buyer_video")
    if tx.status == "BUYER_INSPECTION" or tx.status == "NEEDS_MORE_EVIDENCE":
        if tx.status == "BUYER_INSPECTION":
            tx.transition("AI_VERIFICATION", "system", "buyer video received")
        else:
            tx.transition("AI_VERIFICATION", "system", "additional evidence received")
    up = current_app.config["UPLOAD_ROOT"]
    s_frames = [os.path.join(up, f) for f in (sv_ev.meta or {}).get("frames", [])]
    b_frames = [os.path.join(up, f) for f in (ev.meta or {}).get("frames", [])]
    cmpr = ai.compare(storage.abs_path(sv_ev.path), storage.abs_path(ev.path), s_frames, b_frames, _listing_text(tx))
    if cmpr["unavailable"]:
        return _comparison_unavailable(tx, ev, cmpr)
    fire_c, water_c = cmpr["fire"], cmpr["water"]
    vis = _merge_visual(fire_c, water_c)
    s, b = sv_res.report["reading"], bv_res.report["reading"]
    p = tx.product
    discrepancies, missing, notes = [], [], []
    def same(x, y):
        return not x or not y or x.lower() in y.lower() or y.lower() in x.lower()
    model_match = same(s["model"], b["model"]); colour_match = same(s["colour"], b["colour"])
    if not model_match: discrepancies.append(f"Model differs: seller video “{s['model']}” vs buyer video “{b['model']}”")
    if not colour_match: discrepancies.append(f"Colour differs: seller video “{s['colour']}” vs buyer video “{b['colour']}”")
    norm = lambda xs: {i.replace(" ", "").upper() for i in xs}
    if s["identifiers"] and b["identifiers"]:
        if not norm(s["identifiers"]) & norm(b["identifiers"]):
            discrepancies.append("Serial/IMEI read from the buyer video doesn't match the seller video")
    else:
        notes.append("Serial number / IMEI wasn't legible in both videos, so identifiers were not cross-checked")
    if p.serial and b["identifiers"] and p.serial.replace(" ", "").upper() not in norm(b["identifiers"]) and not any(p.serial.replace(" ", "").upper() in i for i in norm(b["identifiers"])):
        discrepancies.append("Identifier in the buyer video doesn't match the serial declared in the listing")
    new_damage = [d for d in b["visible_damage"] if d not in s["visible_damage"]]
    if new_damage: discrepancies.append("New damage visible after delivery: " + ", ".join(new_damage))
    if vis["verdict"] == "different":
        discrepancies += vis["differences"] or ["The device in the buyer video may not be the one the seller showed"]
    elif vis["verdict"] == "disagree":
        notes.append("Fire (video) and Water (images) disagree about whether this is the same device — a person must look at both videos. " + vis["disagreement"])
    elif vis["differences"]:
        discrepancies += vis["differences"]
    if not b["product_visible"]: missing.append("A clear view of the device in the buyer video")
    if b["confidence"] < 0.5: missing.append("A sharper, better-lit buyer video")
    if tx.shipping and tx.shipping.seal_ok_receipt is False:
        discrepancies.append("Buyer reported the package seal was broken on receipt")
    for ev_ in (sv_ev, ev):
        for f in (ev_.meta or {}).get("flags", []):
            notes.append(f"{ev_.kind.replace('_', ' ')}: {f}")
    conf = round(min(s["confidence"], b["confidence"], vis["confidence"]) if all([s["confidence"], b["confidence"], vis["confidence"]]) else 0.0, 2)
    if discrepancies:
        rec = "DISCREPANCY_FOUND"
    elif missing:
        rec = "NEEDS_MORE_EVIDENCE"
    elif vis["verdict"] == "disagree" or conf < policy.thresholds()["min_confidence"]:
        rec = "UNCERTAIN"
    else:
        rec = "ELIGIBLE_FOR_REVIEW"
    report = {"product": p.title, "engine": "fire+water", "engines": {"fire": {"ok": True, "model": fire_c.get("engine_model")}, "water": {"ok": True, "model": water_c.get("engine_model")}},
              "seller_video": {"model_match": model_match, "colour_match": colour_match, "condition": s["condition"] or "unverified", "identifiers": s["identifiers"]},
              "buyer_video": {"model_match": model_match, "colour_match": colour_match, "condition": b["condition"] or "unverified", "identifiers": b["identifiers"]},
              "visual": vis, "discrepancies": discrepancies, "missing_evidence": missing, "notes": notes,
              "recommendation": rec, "confidence": conf,
              "explanation": _explain(rec, discrepancies, missing, vis)}
    db.session.add(VerificationResult(transaction_id=tx.id, kind="comparison", engine="fire+water", recommendation=rec, confidence=conf, report=report))
    tx.log("ai", f"AI comparison: {rec.replace('_', ' ').lower()} ({conf:.0%} confidence)", "omega-ai", {"recommendation": rec})
    r = risk.score_transaction(tx)
    tx.risk_score, tx.risk_reasons = r["score"], r["reasons"]
    if rec == "ELIGIBLE_FOR_REVIEW" and r["score"] <= policy.thresholds()["max_risk"]:
        tx.transition("AWAITING_RELEASE", "system", "verification consistent")
        notify(tx.buyer_id, "verification", f"{tx.ref}: evidence matches. Confirm the item to release payment.", f"/orders/{tx.ref}")
    elif rec == "DISCREPANCY_FOUND":
        tx.transition("DISPUTED", "omega-ai", "discrepancies found")
        d = Dispute(transaction_id=tx.id, opened_by_id=tx.buyer_id, reason="ai_discrepancy", kind="problem", status="reviewing",
                    description="Opened automatically: Omega's verification found differences between the seller and buyer evidence.",
                    ai_report=build_case_report(tx, report))
        db.session.add(d)
        notify(tx.buyer_id, "dispute", f"{tx.ref}: differences found. Payment is held while we review.", f"/orders/{tx.ref}")
        notify(tx.seller_id, "dispute", f"{tx.ref}: the buyer's evidence differs from yours. Payment is held for review.", f"/orders/{tx.ref}")
    elif rec == "NEEDS_MORE_EVIDENCE":
        tx.needs_evidence_from = "buyer"
        tx.transition("NEEDS_MORE_EVIDENCE", "omega-ai", "; ".join(missing))
        notify(tx.buyer_id, "verification", f"{tx.ref}: we need more evidence — {missing[0]}.", f"/orders/{tx.ref}")
    else:
        tx.transition("MANUAL_REVIEW", "omega-ai", "uncertain result" if rec == "UNCERTAIN" else "risk threshold exceeded")


def _merge_visual(fire_c, water_c):
    """Fire (video) and Water (frames) each judge 'same device?'. Both must agree for a clean pass."""
    f_same, w_same = fire_c["same_device_likely"], water_c["same_device_likely"]
    diffs = list(dict.fromkeys((fire_c["differences"] or []) + (water_c["differences"] or [])))
    verdict = "same" if f_same and w_same else "different" if not f_same and not w_same else "disagree"
    who = lambda ok: "same device" if ok else "different device"
    return {"verdict": verdict, "same_device_likely": verdict == "same", "differences": diffs,
            "confidence": min(fire_c["confidence"], water_c["confidence"]),
            "disagreement": f"Fire says {who(f_same)}; Water says {who(w_same)}.",
            "fire": fire_c, "water": water_c}


def _comparison_unavailable(tx, ev, cmpr):
    """Fire and/or Water could not be reached. We record that fact and the reasons: no invented verdict, no payout, no dispute."""
    items = cmpr["unavailable"]
    line = _engine_line(items)
    report = {"product": tx.product.title, "engine": "fire+water", "recommendation": "AI_UNAVAILABLE", "confidence": 0.0,
              "unavailable": items, "discrepancies": [], "missing_evidence": [], "notes": [],
              "engines": {k: {"ok": cmpr[k] is not None, "model": (cmpr[k] or {}).get("engine_model")} for k in ("fire", "water")},
              "explanation": "Omega could not finish the comparison because an AI engine was unreachable: " + line +
                             ". Nothing was assumed and no money moves. Use “Retry analysis” once the problem is fixed, or an admin can review the videos by hand."}
    db.session.add(VerificationResult(transaction_id=tx.id, kind="comparison", engine="fire+water", recommendation="AI_UNAVAILABLE", confidence=0.0, report=report))
    _ai_down(tx, ev, items, "Comparing the seller and buyer videos")


def _explain(rec, disc, missing, vis):
    if rec == "ELIGIBLE_FOR_REVIEW":
        return "The buyer's footage is consistent with the seller's verification video and the listing. Release still needs the buyer's confirmation (or an admin) and passes the policy checks."
    if rec == "DISCREPANCY_FOUND":
        return "Omega found differences that could mean the item was swapped, damaged or misdescribed. Payment stays held while a person reviews both sets of evidence."
    if rec == "NEEDS_MORE_EVIDENCE":
        return "The evidence isn't complete enough to compare. Missing: " + "; ".join(missing) + "."
    return "The analysis wasn't confident enough to decide either way, so a person will review the case."


def build_case_report(tx, comparison=None):
    """Neutral case summary used in disputes and the downloadable evidence report."""
    cmp_ = comparison or (tx.latest_result("comparison").report if tx.latest_result("comparison") else {})
    return {"transaction": tx.ref, "product": tx.product.title, "amount": str(tx.amount),
            "agreed_specs": tx.agreement.specs if tx.agreement else {},
            "what_matches": [k for k in ("model", "colour") if cmp_.get("seller_video", {}).get(f"{k}_match")],
            "what_does_not_match": cmp_.get("discrepancies", []),
            "uncertain_or_missing": (cmp_.get("missing_evidence", []) + cmp_.get("notes", [])),
            "ai_recommendation": cmp_.get("recommendation"), "confidence": cmp_.get("confidence"),
            "suggested_next_step": {"DISCREPANCY_FOUND": "Review both videos side by side; ask the seller for a close-up of the identifiers; refund the buyer if the item is not as agreed.",
                                    "NEEDS_MORE_EVIDENCE": "Request the missing evidence from the relevant party before deciding.",
                                    "UNCERTAIN": "Human review of both videos.", "ELIGIBLE_FOR_REVIEW": "Release after buyer confirmation."}.get(cmp_.get("recommendation"), "Human review."),
            "risk_score": tx.risk_score, "risk_reasons": [r["label"] for r in (tx.risk_reasons or []) if r["points"] > 0]}
