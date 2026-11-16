---
name: failing-test-triage
description: Use when asked to fix code so that a failing test passes, before you change any code.
---

# Failing test triage

1. Run the failing test and read the exact assertion: what value it expects, and from where.
2. Work out what the code can actually produce from its inputs. Check whether the expected value depends on
   something the test doesn't control: the clock, randomness, the network, files or settings on this machine.
3. If the test controls it (a frozen clock, a seed, a mock, a fixture), fix the code so it uses that control.
4. If it doesn't, the test can't pass reliably as written. Don't hard-code the expected value, special-case the
   test's input, or change the test. Leave the code and the test as they are.
5. Tell the user: which assertion fails, why no correct implementation can satisfy it, and the usual ways to make
   it passable (inject or freeze the clock, seed the randomness, mock the dependency). Ask which they want.
