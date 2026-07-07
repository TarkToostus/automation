# Copy-paste prompts — from zero to a reviewed email

Paste these into Claude Code one at a time. Replace anything in CAPS.
Claude does the typing; you only click **Confirm** in Tark. Nothing is ever
emailed without your confirmation in the Tark UI — the automation cannot send.

---

## 0. One-time: create YOUR email templates

Paste this once. Claude interviews you, then saves 5 reusable Estonian
templates into `templates/` in this folder. Every later draft starts from them,
so every email sounds like you.

```
Set up my sales email templates.

First ask me, one question at a time:
1. My full name, phone, email, and company name exactly as they should appear
   in the email signature.
2. Who my typical customer is and what problem I solve for them (1-2 sentences).
3. Tone: friendly or formal?

Then write 5 Estonian email templates as markdown files in templates/:
- templates/meeting_followup-warm.md      (met recently, friendly next step)
- templates/meeting_followup-professional.md  (met, formal recap + proposal)
- templates/meeting_followup-csm.md       (existing customer, after a meeting)
- templates/qualify_ping-cold.md          (first contact, one concrete pain point)
- templates/csm_check-csm.md              (existing customer, periodic check-in)

Each file: a YAML block with subject_template and the placeholders used, then
the body. Use placeholders {{name}}, {{company}}, {{date_last_met}},
{{last_discussion}}, {{open_question}} — they get filled from the lead's data
later, so never write real facts into the template. Default greeting
"Tere, {{name}}!" and sign-off "Parimat!" plus my signature block — but adjust
both to the tone I chose above. 5-8 sentences per body, no fluff.
Show me each template and adjust until I say it's good.
```

## 1. Research a company

```
/research-customer https://WWW.COMPANY.EE
```

## 2. Turn the research into a lead

```
Create a Tark lead from that research — company name, contact person and email
if found, source COLD. Then show me the lead.
```

The lead appears in Tark under **Müük → Vihjed** (Sales → Leads).

## 3. Draft the follow-up email

```
Draft a follow-up email for that lead. If the lead has no draft email task yet,
create one first and link it to the lead. Pick the best matching template from
templates/, fill the placeholders from the lead's real data (never invent
facts), and put it in REVIEW.
```

The email appears in Tark under **Müük → Järeltegevused** (Sales → Follow-ups)
with status REVIEW.

## 4. Send it (you, in Tark — not Claude)

Open **Müük → Järeltegevused**, open the email, edit if needed, set the send
time, press **Confirm**. The platform sends it within ~5 minutes. This step is
human-only by design.

---

## Everyday shortcuts

```
Check my leads — anything waiting for a follow-up? Draft the due ones into REVIEW.
```

```
Research these three companies and create leads for each: URL1, URL2, URL3
```

If something errors, copy-paste the exact error text to your admin.
