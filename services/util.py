import random

CHALLENGES = [
    "Hold the device next to a paper showing today's date",
    "Say the word “{w}” out loud while showing the screen",
    "Write “OMEGA-{n}” on paper and show it beside the device",
    "Show the device, then flip it over and tilt it toward a window",
    "Hold up {k} fingers next to the device for three seconds",
]


def make_challenge():
    return random.choice(CHALLENGES).format(w=random.choice(["amber", "falcon", "river", "copper", "lantern"]),
                                            n=random.randint(1000, 9999), k=random.randint(2, 5))
