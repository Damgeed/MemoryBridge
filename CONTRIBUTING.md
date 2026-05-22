# Contributing to Memory Bridge

> **Every role matters.** Whether you're a dreamer with big ideas, a critic with sharp eyes, an architect with deep expertise, or a builder who ships — there's a place for you here.

Memory Bridge is built and maintained by a team of four personas (and you):

| Role | Persona | What they do |
|---|---|---|
| **Dreamer** 🌟 | Nova | Big ideas, north star, "what if" questions |
| **Critic** ⚡ | Rex | Pokes holes, finds cracks, stress-tests assumptions |
| **Architect** 🧠 | Henry | Technical depth, clean abstractions, security review |
| **Executor** 🚀 | Fred | Sprint planning, shipping, customers, milestones |
| **Orchestrator** 🧢 | Bud | Coordination, community, CI/CD, keeping things moving |

**You can embody any of these roles when contributing.** Pick the one that fits your style.

---

## 📋 Issue Labels

| Label | Persona | When to use |
|---|---|---|
| `nova/dream` | 🌟 Nova | New feature ideas, visionary enhancements |
| `rex/concern` | ⚡ Rex | Bugs, edge cases, production risks |
| `henry/tech-debt` | 🧠 Henry | Architecture improvements, refactoring, performance |
| `fred/ship` | 🚀 Fred | Sprint tasks, execution items |
| `bud/orchestrate` | 🧢 Bud | Documentation, CI/CD, community |
| `good-first-issue` | 🌱 Anyone | Beginner-friendly, well-scoped tasks |

---

## 🚀 How to Contribute

### 1. Find Your Role

- **Want to propose a feature?** Open a **Nova's Dream** issue — describe the vision, what problem it solves, and what success looks like.
- **Found a bug?** Open a **Rex's Concern** issue — include steps to reproduce, expected vs actual behavior, and environment details.
- **See technical debt?** Open a **Henry's Improvement** issue — describe the current problem, your proposed solution, and trade-offs.
- **Ready to ship?** Pick any open issue, implement it, and open a PR.

### 2. Development Setup

```bash
# Clone the repo
git clone https://github.com/Damgeed/MemoryBridge.git
cd MemoryBridge

# Set up your environment
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run the tests
pytest tests/ -v

# Run the smoke test (start the server first)
memory-bridge &
bash smoke_test.sh
```

### 3. Make Your Changes

- Follow the existing code style (PEP 8, type hints, docstrings)
- Write tests for new functionality (see existing tests for patterns)
- Keep PRs focused — one feature/fix per PR
- Update documentation if you change behavior

### 4. Open a Pull Request

```markdown
## What does this PR do?

Brief description of the change.

## Type of change

- [ ] 🐛 Bug fix (Rex's Concern)
- [ ] ✨ New feature (Nova's Dream)
- [ ] 🛠️ Refactor / tech debt (Henry's Improvement)
- [ ] 🚀 Sprint task (Fred's Ship)
- [ ] 📚 Documentation (Bud's Orchestration)

## How was it tested?

- [ ] Unit tests added/updated
- [ ] Smoke test passes
- [ ] Manual testing steps described

## Related issues

Closes #...
```

### 5. PR Review Flow

1. **Automated checks** — CI runs tests, lints, and smoke tests
2. **Persona review** — Your PR gets reviewed by the relevant persona lens:
   - Features → Nova checks the vision aligns
   - Bug fixes → Rex checks edge cases are covered
   - Refactors → Henry checks architecture integrity
   - Shipping → Fred checks it's actually shippable
3. **Merge** — Once approved, Bud merges it

---

## 💡 Code of Conduct

All contributors must follow our [Code of Conduct](./CODE_OF_CONDUCT.md). Be excellent to each other.

## 🐛 Security

Found a security vulnerability? See [SECURITY.md](./SECURITY.md) for our disclosure process.

## 📜 License

Memory Bridge is MIT licensed. See [LICENSE](./LICENSE) for details.
