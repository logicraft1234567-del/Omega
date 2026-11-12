OMEGA — Buy Safely, Verify the Product, Get Paid
Let me explain OMEGA in simple English.
1. What is OMEGA?
OMEGA is a website that makes buying and selling used phones, laptops and other electronics safer.

Imagine you want to buy an iPhone from someone you don't know.

You send them money, but:
- They might send you a fake iPhone.
- They might send you a damaged phone.
- They might send a completely different phone.
- They might refuse to send anything after receiving your money.
OMEGA is designed to prevent these problems.

The idea is simple: The buyer pays, OMEGA holds the money, AI checks the product, and the seller only gets paid when everything is verified.
2. How does OMEGA work?
There are five main steps.










Step 1: Buyer purchases a product

The buyer finds a phone or laptop they want and clicks Buy with Protection.

They pay through PayPal, but the seller doesn't receive the money yet.













Step 2: Seller records a video

The seller records a video showing the actual product.

OMEGA gives them a random instruction, such as showing the back of the phone and saying a special code.

AI checks the phone's model, storage, appearance and other visible details.













Step 3: Buyer receives and records the package

When the package arrives, the buyer records themselves opening it.

This gives OMEGA evidence of what was actually delivered.













Step 4: Two AI systems inspect the videos

OMEGA uses two AI services:
- Gemini: Watches and analyses the actual videos.
- Groq: Examines pictures taken from the videos.
They independently check whether the product the seller recorded is the same product the buyer received.













Step 5: Payment is released

If the checks pass and the buyer confirms everything is correct, OMEGA sends the money to the seller through PayPal.

If something is wrong, the payment stays on hold while the issue is investigated. An administrator can refund the buyer or resolve the dispute.



3. How does OMEGA use PayPal?
PayPal is a very important part of OMEGA.

It handles three major things:
PayPal feature	What it does
Payments	Collects money from buyers.
Payouts	Sends money to sellers after verification.
Refunds	Returns money to buyers when necessary.

OMEGA also uses PayPal notifications to keep track of payment updates.

There is an important security rule: receiving a PayPal notification alone can never trigger a seller's payout.
4. How does OMEGA use AI?
OMEGA uses two AI systems that work independently.










Gemini — Fire

Watches the seller's video and the buyer's unboxing video.

It checks the product's appearance and details.













Groq — Water

Examines pictures extracted from both videos.

It provides a second opinion and helps identify suspicious product images.



Both AI systems must agree that the products match.

If they disagree, OMEGA sends the case for human investigation.

Important: AI helps identify possible fraud, but it cannot guarantee that a phone is genuine or that nobody has tampered with it.
5. What happens if something goes wrong?
For example:

A buyer orders an iPhone 15 Pro, but the seller sends an iPhone 13.
1. The buyer records the unboxing.
2. Gemini and Groq analyse the evidence.
3. The systems detect that the products do not match.
4. OMEGA prevents the payment from being released.
5. An administrator reviews the case.
6. The buyer can receive a refund through PayPal.
OMEGA also keeps records of the evidence and decisions.
6. What technologies does OMEGA use?
These are the technologies used to build the platform.
Technology	Purpose
Python	Main programming language
Flask	Runs the website and backend
HTML, CSS, JavaScript	Website design and interactions
SQLite	Local database
PostgreSQL	Production database
Gemini API	Analyses videos
Groq API	Analyses video frames
OpenCV	Extracts pictures from videos
PayPal API	Handles payments, payouts and refunds
Render	Hosts the website

7. What pages and features does OMEGA have?
OMEGA has three main types of users.

Buyers
- Create accounts and log in.
- Browse available products.
- Purchase products safely.
- Track orders.
- Upload unboxing videos.
- View AI verification results.
- Confirm deliveries.
- Report problems.
Sellers
- Create seller accounts.
- Upload product listings.
- Set prices and product descriptions.
- Record verification videos.
- Track orders.
- Upload shipping information.
- Receive payments after verification.
Administrators
- View all orders.
- Investigate suspicious transactions.
- Review AI results.
- Handle disputes.
- Refund buyers.
- Release payments when appropriate.
- Monitor AI system failures.
8. How do we deploy OMEGA?
OMEGA can be hosted on Render.

The basic process is:
1. Upload the project to GitHub.
2. Connect GitHub to Render.
3. Create a website service.
4. Set up a PostgreSQL database.
5. Add your PayPal, Gemini and Groq API keys.
6. Deploy the website.
One important limitation: Render's free storage can disappear when the application is redeployed. A production version needs proper permanent video storage, such as Amazon S3.
9. Important limitations
There are a few things to understand before launching OMEGA to real customers.
- PayPal in Nigeria: Nigerian PayPal accounts cannot receive live payouts. You can use PayPal Sandbox for the hackathon, but you'll need another supported payment provider for Nigerian sellers.
- AI accuracy: AI cannot guarantee that a device is genuine or detect every possible form of fraud.
- Storage: Videos need permanent, secure storage for a real commercial platform.
- PayPal approval: Some advanced marketplace payment features require PayPal's approval.
- Email: Email verification and password-reset functionality still need to be added.
10. How do we demonstrate OMEGA to the hackathon judges?
The demonstration should take approximately three minutes.
1. Show a buyer purchasing a phone.
2. Show PayPal collecting the payment.
3. Show that the money is being held.
4. Log in as the seller and upload a product video.
5. Show Gemini and Groq analysing the product.
6. Log in as the buyer and upload an unboxing video.
7. Show both AI systems confirming that the products match.
8. Show PayPal sending the money to the seller.
9. Repeat with a different product video to demonstrate how OMEGA detects a mismatch and prevents payment.