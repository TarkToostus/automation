# Tark Automation — agent guide

You are helping a salesperson research prospects and push them into the **Tark
Platform** as leads + follow-up emails. Everything you do here runs through
`./tark_cli.py` (a single-file, stdlib-only Python CLI — no pip installs) and the
gate-safe `./sales_followup.py` helper.

## The loop
1. **Research** a company → `/research-customer <url>` (built-in web search; writes a dossier).
2. **Create a lead** → `./tark_cli.py leads create --title "..." --company "..." --pipeline Imports --source COLD`
3. **Draft a follow-up** → `./sales_followup.py draft <lead-id> --subject "..." --body "..."`
   (lands as a REVIEW email on Sales → Follow-ups).
4. **Human confirms + sends** in the Tark UI. You can NEVER send mail or set
   CONFIRMED/SENT — the PAT is blocked from those states by design. Don't try.

## Email templates

- If `templates/*.md` exist, every follow-up draft STARTS from the best-matching
  template (file name = `{type}-{tier}.md`; YAML block lists subject_template +
  placeholders). Fill `{{name}}`, `{{company}}`, `{{date_last_met}}`,
  `{{last_discussion}}`, `{{open_question}}` from the lead's real data only.
- If `templates/` is empty, offer the one-time setup prompt from `PROMPTS.md` §0
  before drafting freehand.

## Rules
- Config lives in `~/.config/tark/config.json` (set via `./tark_cli.py config set ...`).
  If a call 401s, the PAT/URL is wrong — tell the user to re-run the two `config set` lines.
- Discover commands with `./tark_cli.py --help` or `./tark_cli.py <cmd> --help`.
  The generic escape hatch is `./tark_cli.py api <path>` (GET) / `--post '{...}'`.
- Keep emails in the customer's language (Estonian prospects → Estonian copy).
- Never invent registry codes, revenue, or grant facts — only state what web
  search actually returned; mark unknowns as unknown.
