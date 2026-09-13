# Copilot Instructions for Codebreakers

## Project Overview & Architecture
Codebreakers is a modular Python cryptographic and cryptanalysis project built on Clean Architecture principles with inward-pointing dependencies:

- **Domain Layer (`src/codebreakers/domain/`)**: Pure business logic, value objects, domain models, error definitions, and protocol interfaces. Zero external framework dependencies (no FastAPI, SQLAlchemy, CLI frameworks, or cloud SDKs).
- **Application Layer (`src/codebreakers/application/`)**: Application services, use cases, and ports coordinating domain models and protocols.
- **Adapters / Infrastructure / Presentation Layer**: CLI (Typer), HTTP API (FastAPI), persistence (SQLAlchemy/Alembic), messaging, and telemetry adapters depending on application ports.

---

## Branching & Workflow Strategy
- **Phase Feature Branches**: Code changes for each phase being worked on must be placed in their own dedicated feature branch using the prefix:
  `phase-{phase_number}-short-description`
  *Examples:* `phase-0-engineering-baseline`, `phase-1-caesar-cipher`, `phase-2-cli-interface`
- **Release tags are separate**: the `v0.1`-`v1.0` identifiers in the roadmap are git tags applied at release milestones, not branch names. Do not reuse the `v{n}` form for phase branches.
- **Checkpoint the roadmap**: Update `TODO.md` in the active phase to check off each item that has been implemented, clarified, or verified, so the task list accurately reflects completed work.
- Ensure tests, type checking, and linting pass before merging to `main`.

---

## Core Engineering Principles
- **SOLID**:
  - **S**ingle Responsibility: Each class, module, and function has one well-defined purpose.
  - **O**pen/Closed: Extend functionality via protocol implementations (e.g., `Cipher` protocols) without altering existing core behavior.
  - **L**iskov Substitution: Concrete implementations must adhere strictly to defined protocols and domain contracts.
  - **I**nterface Segregation: Keep protocols focused and fine-grained (e.g., separate encryption/decryption protocols from cryptanalysis).
  - **D**ependency Inversion: High-level modules depend on abstractions (protocols/ports), not concrete implementations.
- **DRY (Don't Repeat Yourself)**: Eliminate duplicate logic and calculations across cipher implementations and utilities.
- **KISS (Keep It Simple, Stupid)**: Keep implementations straightforward, explicit, and readable. Avoid unnecessary layers of indirection.
- **Favour Reusability over Complexity**: Design modular, cohesive components with clean interfaces. Prefer composition and reusable primitives over complex inheritance hierarchies or premature optimization.

---

## Python Coding Standards & Patterns
- **Target Python Version**: Python 3.12+
- **Typing & Strictness**:
  - Enforce strict typing (`mypy --strict`). All function signatures, arguments, and return types must be explicitly typed.
  - Use modern typing constructs (`collections.abc`, `typing.Protocol`, `typing.Self`, `typing.override`, union syntax `|`, etc.).
- **Immutability & Domain Models**:
  - Prefer immutable value objects using `@dataclass(frozen=True)` or Pydantic models (at boundary layers).
- **Style & Linting**:
  - Adhere to Ruff linting and formatting rules (`line-length = 88`, imports sorted, clean idiomatic Python).
  - Follow PEP 8 naming conventions (e.g., `snake_case` for functions/variables, `PascalCase` for classes/protocols, `UPPER_CASE` for constants).
- **Error Handling**:
  - Define custom domain exceptions in `domain/errors.py`.
  - Avoid catching generic `Exception` unless at top-level boundary handlers.

---

## Testing & Quality Gates
- Write thorough tests under `tests/` using `pytest`.
- Use example-based tests, boundary/edge-case tests, and property-based testing (`Hypothesis`) where appropriate.
- Verify code with:
  - `ruff check .` and `ruff format --check .`
  - `mypy src tests`
  - `pytest`
