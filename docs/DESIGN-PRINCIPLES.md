# Design Principles

> Adopted from the user's standing framework. Where it names seams such as MCP or RPC as examples, this project's seams are the browser page and the CLI client (`PLAN.md` §4).

These principles describe the character of the system we are building. They are not a prescribed implementation and should not force unnecessary machinery into simple features. They guide architectural choices when several reasonable implementations exist. Ownership structure itself is defined in `ARCHITECTURE.md`.

---

## 1. One Application, Many Participants

The human user and software agents operate the **same application**. The human may use a GUI; an agent may use MCP, CLI, RPC, or another machine-facing seam. These are different entrances into one system, not separate implementations of it, and all of them operate against the same authoritative state, capabilities, lifecycles, and rules.

Do not create a "human application" and an "agent application" that merely try to stay synchronized.

- **Collaborate through the application.** Where practical, an agent uses the same meaningful operations the application itself uses — opening a project, running a transformation, creating an artifact, changing configuration, advancing a workflow, inspecting state — through the application's canonical behavior, not an external reimplementation. Agent access is another control surface over the application, not a separate automation system attached beside it.
- **Interfaces are projections, not owners.** A GUI does not own business state because it displays it; an agent interface does not own business behavior because it exposes it. Interfaces project, request, observe, and control capabilities through the shared spine. Closing a GUI should not destroy application truth; changing the machine-facing interface should not require rewriting domain behavior.
- **Parity of meaning, not of controls.** Human and agent interfaces need **semantic parity** where collaboration requires it, not identical controls: a GUI button and an MCP operation may invoke the same capability. Do not force machine interfaces to imitate GUI interactions, or the GUI to expose low-level machine primitives. Tailor each interface to its participant while converging on shared semantics.

## 2. One Reality, and Every Fact Has a Home

There is one authoritative representation of the application's current reality. The GUI, agent interfaces, logs, projections, and derived views may present it differently, but they do not independently define it. When the human changes something, the agent can observe the result; when the agent changes something through an authorized operation, the human can observe it. Collaboration depends on both acting upon and perceiving the same machine.

Every important piece of state has an identifiable authoritative owner. Ask:

> Where does this fact live? Who is allowed to change it?

Derived views may cache or project state, but authority stays clear. Avoid duplicated writable state whose consistency depends on participants "remembering" to synchronize it.

## 3. A Shared Spine, and No Hidden Second System

The application has a coherent internal spine through which meaningful activity passes:

**Participants → Interfaces → Application Spine → Domain Owners → Capabilities → State / Effects**

The implementation may vary, but important operations converge on shared pathways instead of being reimplemented by each interface. The spine provides common state, common behavior, common authorization and validation, observable operations, consistent lifecycle handling, shared history, and coherent interaction between subsystems. Avoid uncontrolled side channels that bypass it without a strong reason.

Do not introduce infrastructure that quietly becomes an independent control plane beside the application: agent-only state databases, automation-only mutation paths, UI-specific business logic, duplicate workflow engines, or hidden background state that cannot be inspected through the application. If such a subsystem is genuinely required, define how it participates in the shared application model rather than letting it become a second source of truth.

## 4. Observable, Attributable, Legible

Meaningful actions leave observable consequences. It should be possible to answer:

- What is happening, and what just happened?
- Who or what initiated it?
- Which lifecycle is active, and what state changed?
- What operation produced this result?
- What is waiting, running, completed, failed, or blocked?
- What is the application doing next?

The goal is **operational intelligibility**, not exhaustive logging of every function call: the human and agent orient themselves from observable state rather than reconstructing activity from hidden implementation details.

Where useful, operations keep enough context to identify their origin — human, agent, internal lifecycle transition, or scheduled/system. This is not primarily an audit feature; it supports collaboration, so that when the application changes while two participants work with it, each can understand why.

Taken together, a developer, user, or agent should be able to form a coherent picture of the running system: what it knows, what it is doing, which subsystem owns each responsibility, how information moves, where state changes, which lifecycles are active, how interfaces reach capabilities, and how changes become visible elsewhere — a connected whole, not a collection of features sharing a process.

## 5. Transparent Lifecycles and Pipelines

Operations with meaningful lifecycles expose them explicitly. Transitions such as **proposed → approved → running → completed** or **queued → executing → failed → retrying** exist as observable, queryable application concepts, not buried control flow.

- Failures are visible as failures.
- Waiting does not look like completion.
- Unknown does not silently become empty.
- Stale is distinguishable from current.

When information or work moves through meaningful stages, the pipeline stays understandable: what entered it, which stage owns the work now, what transformation occurred, what was produced, what failed or was skipped, and what the next stage receives. Intermediate representations may stay internal when they have no operational significance. Do not expose machinery for its own sake; expose the boundaries that matter for understanding and controlling the system.

## 6. Explicit Communication: Commands, Observations, Events

Subsystems are not isolated islands. Meaningful changes propagate through explicit application mechanisms — events, state transitions, commands, requests, results, registered capabilities, shared projections — so the system reacts coherently to its own activity without interfaces manually coordinating unrelated pieces. Prefer explicit communication over hidden coupling, and observable messages or transitions over distant components mutating one another's internals.

Keep three things distinct, because they have different semantics:

- a **command** expresses intent (a request to change something);
- a **query** observes what currently exists;
- an **event** reports a transition or fact that happened.

Events describe reality; they do not replace ownership. The component or manager that owns a capability remains authoritative for performing and validating it; others observe resulting events and update their own state or projections. Prefer

**owner performs operation → state changes → event describes change → interested systems react**

over

**broadcast request → whichever listener happens to respond becomes the behavior**

unless the latter is deliberately part of the design.

When choosing between implicit behavior that needs implementation knowledge to understand and explicit behavior that exposes ownership, transition, or communication, prefer the explicit design unless the added structure would be disproportionate to the problem.

## 7. Grow the Spine With the Product

Do not build an elaborate universal event bus, workflow engine, command architecture, or state framework before the product requires it. Begin with the smallest shared pathway that preserves these principles, and let the spine become more capable as real application behavior demands it. Transparency should clarify the system, not bury it beneath ceremony.

The principle is mandatory. The machinery is not.

---

# Design Test

When introducing a new capability, ask:

1. Who owns this capability?
2. Where does its authoritative state live?
3. How does a human reach it?
4. How does an agent reach it?
5. Do both entrances converge on the same underlying behavior?
6. How does the rest of the application learn that something changed?
7. Is any meaningful lifecycle visible?
8. Can both participants determine what the system is currently doing?
9. Have we introduced duplicated state or a hidden second control path?
10. Is this implementation the smallest structure that preserves the shared-system model?

---

# Core Principle

**Build one coherent application with multiple participants, not multiple applications that happen to cooperate.**

The human and agent interact with the same underlying machine through interfaces appropriate to each. The application has a shared spine connecting its capabilities, state, lifecycles, pipelines, events, and projections. Meaningful activity is observable enough that the system, the human, and the agent stay oriented to the same reality.

The result should behave less like a collection of disconnected features and more like one coherent, internally communicating system.
