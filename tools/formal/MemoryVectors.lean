import MemoryVisibility
open EchoSpeak

def main : IO Unit := do
  let ids := ["", "a", "b"]
  let results := Id.run do
    let mut results := #[]
    for active in [false, true] do
      for scope in [Scope.account, .session, .project, .unknown] do
        for owner in ["a", "b"] do
          for session in ids do
            for project in ids do
              for path in ids do
                for askSession in ids do
                  for askProject in ids do
                    for askPath in ids do
                      let row : Memory := ⟨active, owner, scope, session, project, path⟩
                      let context : Context := ⟨"a", askSession, askProject, askPath⟩
                      results := results.push (visible row context)
    return results
  IO.println (toString results.toList)
