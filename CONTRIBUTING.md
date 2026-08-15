# Contributing

## Project language

English is the required language for all project-authored artifacts:

- source code, identifiers, and comments;
- configuration keys and schema fields;
- documentation and architecture records;
- tests, fixture names, and generated diagnostics;
- CLI messages, application logs, and user-facing project text;
- branch names, commit messages, issues, and pull requests.

Discussion outside the repository may happen in Portuguese or another language. That does not change the language used in committed artifacts.

Exceptions are limited to content whose original form is part of the data being represented. Examples include raw provider values, synthetic Portuguese transaction descriptions, legal names, institution names, and established Brazilian terms such as PIX, TED, BRL, and Open Finance Brasil. Keep project field names and explanations in English even when their values use Portuguese.

## Data-contract policy

Project schemas should model the kinds of financial information relevant to income estimation and available through Open Finance. They should not duplicate official Open Finance endpoint schemas by default.

Follow these rules:

1. Use stable, project-owned English names based on domain meaning.
2. Include only fields needed by simulation, estimation, evaluation, or auditability.
3. Keep provider-specific and official-payload details behind adapters.
4. Version internal contracts independently from external API versions.
5. Preserve enough source metadata to trace an observation without leaking simulator ground truth.
6. Never expose ground-truth-only labels through estimator inputs.

The internal model may organize accounts, balances, transactions, cards, loans, and investments differently from official payloads. Similar domain coverage does not imply wire compatibility or certification as an official Open Finance mock.

## Financial and personal data

Never commit credentials, access tokens, raw client data, or identifiable financial records. Tests should use synthetic data or approved de-identified datasets. Generated data must not reproduce a real person's history.
