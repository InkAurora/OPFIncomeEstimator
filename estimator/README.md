# Estimator

This component estimates a client's income from normalized financial data obtained with consent through Open Finance Brasil.

## Responsibilities

- Validate the estimator input contract.
- Identify likely income credits and exclude likely transfers, reversals, refunds, loans, and other non-income movements.
- Detect recurrence, payer consistency, amount stability, and relevant seasonality.
- Produce an estimate for a defined period, such as monthly income.
- Return evidence, assumptions, warnings, and confidence information with the estimate.
- Version estimation behavior so a result can be reproduced and audited.

## Proposed output characteristics

An estimator result should make these points explicit:

- estimated amount and currency;
- estimation period and observation window;
- gross or net interpretation;
- contributing income streams;
- excluded or ambiguous transactions;
- confidence or uncertainty measure;
- estimator version and execution timestamp.

## Boundary

The estimator consumes a stable internal observation contract inspired by the types of data available through Open Finance Brasil. It does not consume or expose an exact copy of official Open Finance API payloads. Provider-specific fields, endpoint nesting, transport metadata, and schema versions must be translated by an adapter before reaching this component.

Authentication, consent management, token storage, raw-provider transport, and official-payload compatibility do not belong in estimation logic.

Do not add real client records to fixtures. Use synthetic data from `finances_simulator` or approved de-identified datasets.
