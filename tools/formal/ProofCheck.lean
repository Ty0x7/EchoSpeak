import Lean.Elab.BuiltinCommand
import Lean.Elab.Declaration
import Lean.Elab.Tactic.BuiltinTactic
import Lean.Elab.Tactic.Simp
import Lean.Util.CollectAxioms

open Lean Elab Command

set_option warningAsError true
set_option maxHeartbeats 100000
set_option maxRecDepth 512

elab "checkProof " statement:str proof:str : command => do
  let env ← getEnv
  let .ok st := Parser.runParserCategory env `term statement.getString "statement"
    | throwError "Invalid statement term"
  let .ok pr := Parser.runParserCategory env `term proof.getString "proof"
    | throwError "Invalid proof term"
  let term := mkIdent `EchoSpeakChecked
  elabCommand (← `(theorem $term : $(⟨st⟩) := $(⟨pr⟩)))
  let axioms ← Lean.collectAxioms `EchoSpeakChecked
  let allowed := [`propext, `Classical.choice, `Quot.sound]
  if let some ax := axioms.find? (!allowed.contains ·) then
    throwError "Unapproved axiom dependency: {ax}"
  logInfo m!"EchoSpeak kernel accepted; Lean {Lean.versionString}; axioms: {axioms.toList}"
