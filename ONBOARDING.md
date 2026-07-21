# Onboarding — research customers & fire leads from your laptop

You will use **Claude Code** (an AI agent) to research prospects and push them
into Tark as leads + follow-up emails. This guide gets you there with **zero
developer setup** — no Homebrew, no Xcode, no manual `git`.

> **Why this guide exists:** the old instructions started with `git clone`, which
> drags in git → Xcode Command Line Tools → a compiler. On a fresh Mac that chain
> fails silently (`xcode-select --install` "saves the request" and never finishes).
> None of it is actually needed. Pick a path below.

---

## What you need first (everyone)

1. A **Claude account** on a paid plan — Pro, Max, Team, or Enterprise
   (the web agent and bigger models need a paid tier). Sign in at https://claude.ai.
2. A **Tark Personal Access Token (PAT)** — your key to push leads.
   In Tark: **Profile → Security → API keys → Add token**. Copy it once
   (`tark_pat_…`). Treat it like a password. Ask your admin if you don't see it.
3. Your Tark **deployment URL**, e.g. `https://your-deployment.example.com`.

That's it. Now pick **Path A** (nothing to install) or **Path B** (local app).

---

## Path A — Claude Code on the web (recommended, nothing to install) ⭐

Best for: any laptop, locked-down work machines, Windows, "I don't want to touch
a terminal." Runs in a cloud sandbox that already has Python, git, and web search.

1. Open **https://claude.ai/code** in your browser and sign in.
2. Connect GitHub when prompted, then pick the repo **`TarkToostus/automation`**
   (it's public — search for it). The sandbox clones it for you.
3. In the chat, paste this once to configure your token (replace the two values):
   ```
   Run: ./tark_cli.py config set url https://your-deployment.example.com
   Run: ./tark_cli.py config set pat tark_pat_xxxxxxxxxxxxx
   ```
4. Confirm it works — type:
   ```
   Run ./tark_cli.py tasks and show me the result
   ```
   You should see your tasks. ✅ You're done — skip to **The daily workflow** below.

**Pros:** zero install, works on any OS, nothing can "silently fail."
**Cons:** needs a paid Claude plan with web access; your session lives in the cloud.

---

## Path B — Claude Code on your laptop (local app)

Best for: people who want the agent running against local files and are on a
normal (not locked-down) machine.

### macOS / Linux

Run **one** command in **Terminal** (Spotlight → type "Terminal"):

```bash
curl -fsSL https://raw.githubusercontent.com/TarkToostus/automation/main/setup.sh | bash
```

It installs Claude Code (native — **no Node, no Xcode, no Homebrew**), downloads
`tark_cli`, checks Python, and asks for your PAT + URL. Follow the prompts.

### Windows

Open **PowerShell** and run:

```powershell
irm https://raw.githubusercontent.com/TarkToostus/automation/main/setup.ps1 | iex
```

Same effect on Windows — native Claude Code, `tark_cli`, config. No WSL needed.

### After either installer

Open Claude Code in the folder it created (`~/tark-automation`):

```bash
cd ~/tark-automation && claude
```

Confirm: ask Claude `Run ./tark_cli.py tasks`. You should see your tasks. ✅

**Pros:** local files, full terminal power.
**Cons:** a couple of steps; needs Python 3 (the installer handles it, but on a
broken Mac you may have to install Python from the link it prints).

---

## The daily workflow — research → lead → follow-up

> **Copy-paste versions of every prompt below — including a one-time "create my
> email templates" setup — live in [PROMPTS.md](PROMPTS.md).** Start there.

Once either path works, you drive everything in plain language inside Claude Code.
The bundled **`/research-customer`** skill + `tark_cli` do the rest.

**1. Research a prospect.** Type:
```
/research-customer https://www.hekotek.com
```
Claude searches the web (registry, financials, recent news, grants, certs) and
writes a short dossier.

**2. Create the lead.** Type:
```
Create a Tark lead for Hekotek AS from that research — company, pipeline Imports, source COLD.
```
Claude runs `./tark_cli.py leads create …`. It appears on **Sales → Leads**.

**3. Draft the follow-up email.** Type:
```
Draft a first follow-up email for that lead and put it in REVIEW.
```
Claude runs the gate-safe helper (`./sales_followup.py draft …`). It lands as a
card on **Sales → Follow-ups** as a DRAFT/REVIEW email.

**4. A human confirms + sends.** You (or Marek) open **Sales → Follow-ups**, edit
the body, set a send time, and **confirm**. Confirmation is a human-only gate —
the automation can never send mail itself. The platform sender mails it on the
next 5-minute poll. ✅

> Everything Claude does maps to a `tark_cli` command — run `./tark_cli.py --help`
> to see the full list, or just ask Claude "what can tark_cli do?".

---

## Where your work lives (and keeping your token safe)

- **Leads and follow-up emails are saved in Tark**, not on your laptop or in the
  cloud session. Once Claude creates a lead, it's on **Sales → Leads** for good —
  closing the browser tab loses nothing important.
- The only local file is the **research dossier** (`research/{domain}.md`). It's
  just notes — regenerate it any time by re-running `/research-customer`.
- Your **PAT is a password.** Only paste it into the `config set pat` step. Never
  put it in an email, Teams/Slack message, screenshot, or a shared doc. If it
  leaks, ask your admin to revoke it — it only carries *your* permissions and is
  revocable from **Profile → Security → API keys**.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `git: command not found` / Xcode dialog won't finish | You don't need git. Use **Path A** (web) or the **Path B installer** (it uses `curl`, not git). |
| `python3` opens an Xcode install dialog | Path B installer prints a direct **python.org** download link — a normal double-click installer, no terminal. Or just use **Path A**. |
| `401 Unauthorized` from tark_cli | PAT wrong/expired, or wrong deployment URL. Re-run the two `config set` lines. |
| `403` on creating a lead | Your PAT lacks `sales:write`. Ask your admin to widen the token's scopes. |
| Claude says it can't access the web | You're on a free plan or web access is off — upgrade to Pro/Max/Team. |

Stuck? Send your admin the exact error text (copy-paste it) — don't screenshot.
