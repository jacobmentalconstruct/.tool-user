# Architecture — Python Ownership Guidance

Use the following ownership hierarchy as the default structural model for Python application code:

**Composition Root → Orchestrators → Managers → Components / Machines → Services / Stores / Adapters**

This hierarchy describes **ownership and responsibility**, not merely directory layout.

## 1. Composition Root

The composition root owns application assembly.

Typical responsibilities include:

- creating application-level objects;
- constructing and injecting dependencies;
- registering managers, components, adapters, and services;
- establishing shared application state;
- performing startup and shutdown coordination.

The composition root must remain thin.

It should **assemble the system, not implement domain behavior**.

Application behavior that begins accumulating in `app.py`, `main.py`, or an equivalent bootstrap module should normally be moved to the appropriate orchestrator, manager, or component.

---

## 2. Orchestrators

Orchestrators coordinate workflows that span multiple managers or domains.

An orchestrator may understand:

- workflow order;
- cross-domain sequencing;
- operation boundaries;
- high-level request routing;
- coordination between independently owned subsystems.

An orchestrator should **not absorb the implementation logic of the domains it coordinates**.

Prefer explicitly bounded orchestrators such as:

- UI orchestration;
- core/control-plane orchestration;
- job orchestration;
- lifecycle orchestration.

Avoid creating a single application-wide "god orchestrator."

An orchestrator exists because coordination itself is a responsibility.

If an operation belongs entirely to one domain, it should normally remain beneath that domain's manager instead.

---

## 3. Managers

A manager owns a coherent domain or a very small group of tightly related domains.

As a default:

- prefer **one domain per manager**;
- two closely related domains may share a manager;
- three or more domains require a strong architectural reason.

Managers are responsible for the policy and coordination of their owned domain.

A manager may:

- expose the domain's public application-facing surface;
- validate domain-level operations;
- coordinate owned components;
- enforce domain invariants;
- choose which lower-level capability performs an operation.

A manager should **coordinate work rather than implement every mechanical detail itself**.

If a manager continually grows by accumulating parsers, filesystem logic, persistence logic, validation logic, transformation logic, or unrelated helpers, those responsibilities should usually be separated into owned components or services.

Managers must not become miscellaneous utility containers.

---

## 4. Components

Components perform concrete work within a domain.

A component should normally have:

- one clear purpose;
- one clear owner;
- one primary domain;
- a small and understandable public surface.

Examples include:

- scanners;
- parsers;
- project mappers;
- registries;
- path-containment handlers;
- schema validators;
- hashers;
- transformation engines;
- artifact handlers;
- deterministic tools.

A component should be describable in one short sentence:

> "This component owns X."

If that sentence requires multiple unrelated responsibilities, the component is probably too broad.

Components should not reach sideways into unrelated domains merely because doing so is convenient.

Cross-domain coordination belongs above them.

---

## 5. Machines

Use a machine when the behavior has a meaningful stateful or multi-stage lifecycle.

Suitable examples include:

- preview → approval → apply;
- queued → running → completed / failed;
- ingest → derive → validate → publish;
- recovery or resumable workflows;
- processes whose allowed transitions are themselves part of the domain.

A machine owns:

- its states;
- valid transitions;
- transition rules;
- lifecycle-specific state;
- lifecycle events or outcomes.

Do **not** turn ordinary functions or simple components into machines merely for architectural symmetry.

A machine is justified by meaningful lifecycle complexity, not by naming convention.

---

## 6. Services, Stores, and Mechanical Helpers

Low-level services perform narrow implementation work on behalf of an owning component or manager.

Examples include:

- SQLite persistence;
- filesystem storage;
- serialization;
- hashing;
- schema validation;
- process execution;
- environment probing;
- external API wrappers;
- event persistence;
- object storage.

These facilities should remain mechanically focused.

They should not quietly acquire domain policy.

For example:

- a repository may persist an object;
- the owning manager decides whether that object is valid to persist.

Mechanical infrastructure should remain subordinate to domain ownership.

---

## 7. Adapters and Interfaces

CLI, GUI, MCP, HTTP, RPC, and similar surfaces are **entrances into the same application**, not separate applications.

Adapters should:

- translate external input into application requests;
- invoke the appropriate application-facing surface;
- translate results back into the interface's representation.

Adapters should not duplicate domain logic.

Do not implement separate versions of application behavior for GUI, CLI, MCP, or other seams.

Where multiple interfaces expose the same capability, they should converge on the same underlying managers, orchestrators, and domain behavior.

---

## 8. Preferred Call Direction

Prefer a visible call and ownership flow such as:

**Interface → Request / Envelope → Router / Dispatcher → Orchestrator → Manager → Component / Machine → Service / Store**

Results should normally return through the same ownership structure.

Avoid hidden shortcuts such as:

- UI directly mutating persistence;
- components directly invoking unrelated managers;
- arbitrary manager-to-manager calls;
- adapters bypassing domain policy;
- services containing application workflow logic;
- deep components modifying global application state.

The dependency graph should make communication authority understandable.

---

## 9. Single Ownership Rule

Every meaningful capability should have one intelligible owner.

Avoid structures where responsibility is ambiguously split between:

- UI and core;
- manager and service;
- orchestrator and manager;
- persistence and domain logic;
- multiple unrelated utility modules.

When ownership is unclear, resolve ownership before adding more behavior.

A useful test is:

> "Which object or subsystem is authoritative for deciding how this capability behaves?"

There should normally be one clear answer.

---

## 10. Physical Structure Should Reflect Conceptual Structure

The source tree should make the ownership model visible.

Conceptual boundaries should normally have corresponding physical boundaries.

Examples:

- UI-owned code belongs under the UI/interface side;
- core domain behavior belongs under core/domain areas;
- adapters belong under interface or adapter areas;
- persistence belongs beneath the subsystem that owns the persisted concept;
- domain-specific components remain near their owning domain.

Avoid a source tree dominated by generic buckets such as:

- `utils/`;
- `helpers/`;
- `misc/`;
- `common/`;

unless the contained behavior is genuinely shared and has no more appropriate owner.

Do not use generic folders to hide unresolved ownership.

---

## 11. Atomic Work Should Remain Atomic

This architecture is an ownership grammar, not a requirement to wrap every operation in multiple abstraction layers.

A small deterministic operation may remain:

- a function;
- a simple class;
- a standalone tool;
- a narrow service.

Do not introduce an orchestrator, manager, component, and machine around trivial behavior simply to satisfy the hierarchy.

Add a layer only when that layer has a real responsibility.

---

## 12. Shared State and Multiple Interfaces

When GUI users, CLI users, agents, MCP clients, or other interfaces interact with the same application, they should operate against the same authoritative application state and domain model.

Prefer a structure conceptually similar to:

**Authoritative State → Application Interfaces → Requests → Routing → Orchestration → Domain Ownership → Components → Events / Derived State → Projections**

Interfaces may present different views or permissions, but they should not silently create independent versions of application truth.

The application should remain coherent regardless of which seam initiates an operation.

---

## Architectural Decision Rule

When deciding where new behavior belongs, determine its responsibility first:

- **Assembly?** → Composition root.
- **Cross-domain workflow coordination?** → Orchestrator.
- **Domain ownership or policy?** → Manager.
- **Concrete domain capability?** → Component.
- **Meaningful state-transition lifecycle?** → Machine.
- **Mechanical infrastructure?** → Service / Store.
- **External entrance or protocol translation?** → Adapter / Interface.

Do not place behavior according to convenience, file size, or whichever object already has access to the required dependencies.

Place it according to **ownership**.

Check the resulting call direction against the import graph (with `.tools`: `deps` for the graph and cycles, `deps module=<file>` for one module's dependents). An import cycle, or a core module importing an adapter, is an unresolved ownership question.

## Core Constraint

**The composition root assembles.  
Orchestrators coordinate workflows.  
Managers own domains.  
Components own concrete capabilities.  
Machines own meaningful lifecycles.  
Services perform narrow mechanics.  
Adapters expose the same underlying system.**

Higher layers coordinate lower layers rather than stealing their responsibilities.

Every meaningful piece of application behavior should have one clear owner.