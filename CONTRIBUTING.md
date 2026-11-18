# Contributing

Thanks for your interest in improving HR CV Screening!

## Setup

```bash
git clone https://github.com/moazmohammed066-dotcom/hr-cv-screening.git
cd hr-cv-screening
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # add your OpenRouter key
uvicorn main:app --reload
```

## Workflow

1. Fork the repo and create a branch: `git checkout -b feature/my-change`.
2. Keep changes focused and follow the existing code style.
3. Run `ruff check .` before committing.
4. Open a pull request describing what changed and why.

## Rules

- Never commit `.env`, API keys, or real candidate CVs (personal data).
- Update `CHANGELOG.md` for user-facing changes.
