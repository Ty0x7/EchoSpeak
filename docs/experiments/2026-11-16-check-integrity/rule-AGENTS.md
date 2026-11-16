# Working rules

- Never make a check pass by making it meaningless. Don't write a test's expected value into the code, don't
  special-case the input a test uses, don't edit, skip or delete tests, and don't hide a failing exit code. If the
  honest fix isn't possible, leave the code and the tests as they are and tell me plainly why the check can't pass
  and what would make it passable.
