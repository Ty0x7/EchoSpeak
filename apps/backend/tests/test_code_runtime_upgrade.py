"""Deterministic protocol metadata, quota parsing and Git admission regressions."""
import json
from pathlib import Path
import subprocess
import pytest
from agent import code_accounts as accounts, code_worktree as wt, code_limits

def test_runtime_models_pin_resolved_versions_and_merge_default():
    rows=[{'value':'default','resolvedModel':'claude-opus-5-5','displayName':'Default'},
          {'value':'opus','resolvedModel':'claude-opus-5-5','displayName':'Opus 5.5','supportedEffortLevels':['low','high']},
          {'value':'haiku','resolvedModel':'claude-haiku-5-5','displayName':'Haiku 5.5'}]
    models=accounts.normalize_claude_models(rows)
    assert len(models)==2 and models[0]['model']=='claude-opus-5-5'
    assert models[0]['displayName']=='Opus 5.5' and models[0]['isDefault']
    assert models[0]['efforts']==['low','high'] and models[1]['efforts']==[]

def test_metadata_check_sends_no_user_prompt_and_discards_account_secrets(monkeypatch):
    def run(argv,**kw):
        packet=json.loads(kw['input'])
        assert packet['type']=='control_request' and packet['request']['subtype']=='initialize'
        assert '--no-session-persistence' in argv and '{"disableAllHooks":true}' in argv
        return subprocess.CompletedProcess(argv,0,json.dumps({'type':'control_response','response':{'request_id':'echo-models','response':{'models':[{'value':'opus','resolvedModel':'claude-opus-5-5','displayName':'Opus 5.5'}],'account':{'token':'SECRET'}}}}),'')
    monkeypatch.setattr(accounts,'executable',lambda _: 'fixture.exe')
    monkeypatch.setattr(accounts.subprocess,'run',run)
    assert 'SECRET' not in str(accounts.claude_models())

def test_subscription_metrics_do_not_convert_missing_or_invalid_values_to_zero():
    result=code_limits.codex({'rateLimits':{'primary':{'usedPercent':20,'resetsAt':123}}})
    assert result['windows'][0]['used_percent']==20
    for bad in [None,True,float('nan'),101,-1]:
        assert code_limits.codex({'rateLimits':{'primary':{'usedPercent':bad}}})['windows']==[]
    assert code_limits.claude({'status':'rejected'})['windows']==[]
    assert code_limits.claude({'utilization':.25,'resetsAt':123})['windows'][0]['used_percent']==25
    assert code_limits.claude({'status': [], 'resetsAt':123})['resets_at']==123

def test_git_discovery_errors_are_actionable(monkeypatch, tmp_path):
    def missing(*args, **kwargs):
        raise FileNotFoundError()
    monkeypatch.setattr(wt.subprocess, 'run', missing)
    with pytest.raises(ValueError, match='Install Git'): wt.preflight(tmp_path)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('git', 10)
    monkeypatch.setattr(wt.subprocess, 'run', timeout)
    with pytest.raises(ValueError, match='timed out'): wt.preflight(tmp_path)

def test_usage_view_reads_cached_account_limits_without_launching_a_runtime(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from agent.workbench import Workbench
    monkeypatch.setattr(accounts, 'remembered', lambda runtime: {'signed_in':True,'plan':'fixture',
        'subscription_usage':code_limits.codex({'rateLimits':{'primary':{'usedPercent':12}}})} if runtime=='codex' else {})
    owner=SimpleNamespace(sessions={},owned_tools={},tool_loans={},directory=tmp_path/'sessions')
    result=asyncio.run(Workbench(owner).usage({'project_id':'fixture'}))
    assert result['runtimes']==[]
    assert result['accounts'][0]['subscription_usage']['windows'][0]['used_percent']==12

def test_git_preflight_distinguishes_folder_unborn_and_nested_without_changing_files(tmp_path, monkeypatch):
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES',str(tmp_path))
    folder=tmp_path/'repo';folder.mkdir()
    with pytest.raises(ValueError,match='not a Git repository'):wt.preflight(folder)
    wt.git(folder,'init')
    (folder/'owner.txt').write_text('unchanged')
    with pytest.raises(ValueError,match='initial commit'):wt.preflight(folder)
    wt.git(folder,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','add','owner.txt')
    wt.git(folder,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','fixture')
    assert len(wt.preflight(folder))==40
    sub=folder/'sub';sub.mkdir()
    with pytest.raises(ValueError,match='repository root'):wt.preflight(sub)
    assert (folder/'owner.txt').read_text()=='unchanged'
