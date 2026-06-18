---
description: Research a prospect company and prep it as a Tark lead. Argument: company website URL.
---

Research a potential customer using only built-in web search + fetch (no extra
tools needed), then offer to push it into Tark as a lead.

Extract the domain from the URL (e.g. `https://www.hekotek.com/about` → `hekotek.com`)
and derive the company name from the site.

## Step 1 — Website
Use WebFetch on the homepage and likely sub-pages (`/products`, `/services`,
`/tooted`, `/teenused`, `/about`, `/meist`, `/contact`, `/kontakt`). Extract:
what they make, their terminology, certifications, key people.

## Step 2 — Registry & size
- WebSearch: `"{company}" site:inforegister.ee` — registry code, board, ownership
- WebSearch: `"{company}" revenue employees annual report` — size indicators

## Step 3 — Recent signals
- WebSearch: `"{company}" news 2025 OR 2026` — investments, expansions, hires
- WebSearch: `"{company}" certificate ISO CE quality` — standards held

## Step 4 — Grant / digitalization signals (Estonia)
- WebSearch: `"{company}" site:eis.ee` and `"{company}" site:kik.ee`
- WebSearch: `"{company}" digitaliseerimine OR "tootmise juhtimine" OR "tööstus 4.0"`

Flag anything mentioning production-management software, digitalization, or
automation — those are direct Tark selling signals.

## Step 5 — Dossier
Write a short, skimmable dossier (≤1 page): what they do, size, recent signals,
why Tark fits, and a one-line outreach angle. Save it to `research/{domain}.md`.

## Step 6 — Offer the lead
Ask the user: "Create this as a Tark lead?" If yes, run:

```bash
./tark_cli.py leads create --title "{company} — {angle}" --company "{company}" --pipeline Imports --source COLD
```

Then offer to draft a first follow-up email:

```bash
./sales_followup.py draft <lead-id> --subject "..." --body "Tere, {name}! ..."
```

The draft lands as a REVIEW EmailTask on **Sales → Follow-ups**. A human confirms
and sends it — automation never sends mail itself.
