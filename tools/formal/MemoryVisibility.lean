namespace EchoSpeak

inductive Scope where
  | account | session | project | unknown

structure Memory where
  active : Bool
  owner : String
  scope : Scope
  session : String
  projectId : String
  projectPath : String

structure Context where
  owner : String
  session : String
  projectId : String
  projectPath : String

/-- Canonical memory read policy on normalized identifiers. Empty identifies an absent field. -/
def visible (row : Memory) (context : Context) : Bool :=
  row.active && row.owner == context.owner &&
    match row.scope with
    | .account => true
    | .session => context.session != "" && row.session == context.session
    | .project =>
      if row.projectId != "" then row.projectId == context.projectId
      else context.projectPath != "" && row.projectPath == context.projectPath
    | .unknown => false

/-- A read cannot return another owner's record or an inactive record. -/
theorem readRequiresActiveOwner (r : Memory) (c : Context) (h : visible r c = true) :
    r.active = true ∧ r.owner = c.owner := by
  simp only [visible, Bool.and_eq_true, beq_iff_eq] at h
  exact h.1

/-- A change of project cannot reveal an ID-bound row, even at the same path. -/
theorem projectNavigationIsolates (r : Memory) (c : Context)
    (scope : r.scope = .project) (bound : r.projectId ≠ "") (changed : r.projectId ≠ c.projectId) :
    visible r c = false := by
  simp [visible, scope, bound, changed]

/-- Legacy project rows need the original, nonempty project path. -/
theorem legacyPathRequired (r : Memory) (c : Context)
    (scope : r.scope = .project) (legacy : r.projectId = "") (h : visible r c = true) :
    c.projectPath ≠ "" ∧ r.projectPath = c.projectPath := by
  simp [visible, scope, legacy] at h
  exact ⟨h.2.1, h.2.2⟩

/-- Navigating to a different or absent session cannot reveal this row. -/
theorem sessionNavigationIsolates (r : Memory) (c : Context)
    (scope : r.scope = .session) (changed : r.session ≠ c.session) :
    visible r c = false := by
  simp [visible, scope, changed]

/-- Retiring a row revokes reads in every context. -/
theorem retirementRevokes (r : Memory) (c : Context) :
    visible { r with active := false } c = false := by
  simp [visible]

set_option warningAsError true
#print axioms readRequiresActiveOwner
#print axioms projectNavigationIsolates
#print axioms legacyPathRequired
#print axioms sessionNavigationIsolates
#print axioms retirementRevokes

end EchoSpeak
