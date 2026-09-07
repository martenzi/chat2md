# What's the difference between a list and a tuple in

- Source: `sample_gemini.json`
- Mode: CHAT
- Extracted: 2026-09-09 18:20:54
- Messages: 4

---

## User — 2026-09-02 11:00:00

What's the difference between a list and a tuple in Python?

### Model (thinking) — 2026-09-02 11:00:04

> Short conceptual question. Cover mutability, syntax, typical use cases, and performance nuance briefly.

## Model — 2026-09-02 11:00:04

A **list** is mutable and uses square brackets; a **tuple** is immutable and uses parentheses.

- `list`: `[1, 2, 3]` — you can add, remove, or change items.
- `tuple`: `(1, 2, 3)` — fixed after creation.

Use a tuple for fixed records (coordinates, RGB values) and a list when the collection will change. Tuples are also hashable, so they can be dict keys.

---

## User — 2026-09-02 11:01:00

Can a tuple contain a list?

## Model — 2026-09-02 11:01:08

Yes — the tuple itself stays immutable, but the list *inside* it is still mutable:

```python
t = (1, [2, 3])
t[1].append(4)
print(t)  # (1, [2, 3, 4])
```

Just note that such a tuple is no longer hashable, so it can't be used as a dict key.

