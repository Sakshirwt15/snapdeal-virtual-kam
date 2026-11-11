# Snapdeal Virtual KAM: My Submission Note

Hi team, this is my Mini AI Virtual KAM. I have written this note in simple words, the way I explain it myself.

## What I built
A voice bot named **Aarav**. A Snapdeal seller talks to it when their sales drop. Aarav looks at the seller's data, finds out **why** sales fell, asks a few questions, and suggests what to do. After the seller agrees, Aarav also **does the next steps** (like creating a restock task and booking a follow-up call). At the end it speaks a short recap and shows a written summary.

It works both ways:
- **Seller calls the bot** (inbound), or
- **Bot calls the seller first** when it sees a problem in the account (outbound).

## Tools and technology I used
| Part | What I used | Why I chose it |
|---|---|---|
| Speech to text (bot listens) | Browser Web Speech API in Chrome (Hindi and English) | Free, no setup, understands Hinglish |
| AI brain (LLM) | Groq with `gpt-oss-120b` and tool calling (Gemini is kept as a backup option) | Very fast replies, and speed matters a lot in a voice call |
| Text to speech (bot speaks) | Microsoft Edge neural voices through `edge-tts` (Hindi and Indian English voices) | Natural Indian voice, no API key needed |
| Backend | Python FastAPI | Runs the conversation, tools and safety checks |
| Frontend | One simple HTML page | Mic button, live transcript, summary card |

I used a simple pipeline: **listen -> think -> speak**. I did not use a speech-to-speech model because each step is easy to test and replace.

## How a call goes
1. Call starts. The bot checks the seller's numbers and finds the biggest problems first.
2. It tells the main finding and asks the seller about it.
3. It asks 2 or 3 questions, one at a time.
4. It explains the reason with approximate rupee impact. In the sample data the biggest reason is out-of-stock products, then cancellations, and pricing is small.
5. It suggests 2 or 3 actions, most important first, and asks the seller to agree with a date.
6. After the seller says yes, it creates the restock task, books a follow-up call and sends a WhatsApp summary.
7. It speaks a short recap.

## Safety checks I added
- The bot takes an action only after the seller clearly agrees in their own words.
- If the AI says "I created the task" but the tool did not actually run, my code catches it and forces the real action. So the bot does not make false claims.
- Dates and the final summary are made by my code, not by the AI, so they are not wrong or made up.
- The bot says it is an AI and can pass the seller to a human KAM.

## What is real and what I assumed
- **From the assignment data:** sales, orders, cancellation rate, average selling price, out-of-stock count.
- **My assumptions (mock data):** seller profile, the 18 out-of-stock products with prices, cancellation reasons, drop in impressions, and two competitor price gaps.
- I treated "orders" as orders after cancellation. So the split of about Rs 55k (stock-outs) and Rs 9k (cancellations) is an **estimate**.
- WhatsApp, restock task, follow-up and escalation are **mocked**. In a real product they would connect to Snapdeal seller APIs.

## Limitations (being honest)
- Speech recognition needs Google Chrome.
- The AI is a third-party API, so it can sometimes be slow.
- To interrupt the bot, you tap the orb. It does not stop automatically when you speak.
- No login and no database. It is a demo only.

## What I would do next
Automatic interruption while the bot speaks, a real-time speech model for faster replies, more Indian languages, and connecting real seller data.

## How to run
```
pip install -r requirements.txt
cp .env.example .env      # Windows: copy .env.example .env
# put your Groq key in .env
uvicorn server:app --port 8000
```
Open http://127.0.0.1:8000 in Google Chrome, use headphones, and click **Start call**.