"""Direct folder sessions, optional Git, and unchanged explicit worktree isolation.

Owned local protocol/CLI fixtures only. No provider inference or owner files.
"""
import json
from pathlib import Path
import sys
import pytest
from agent import projects, code_sessions as cs, code_worktree as wt
from agent.workbench import Workbench, OwnedJob
from config import config
from tests.test_code_sessions import fake, wait_for
from tests.test_claude_code_runtime import signed_in


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(tmp_path))
    source = tmp_path / 'ordinary-folder'; source.mkdir()
    (source / 'hello.txt').write_text('owner local content')
    manager = projects.ProjectManager(tmp_path / 'projects')
    monkeypatch.setattr(projects, '_project_manager', manager)
    item = manager.create_project('Ordinary folder', workspace_root=str(source), trust_state='trusted')
    monkeypatch.setattr(config, 'privacy_mode', 'standard')
    from agent.lean import stop
    monkeypatch.setattr(stop, 'paused', lambda: False)
    from agent import code_executables
    monkeypatch.setattr(code_executables, 'executable', lambda _: sys.executable)
    service = cs.Sessions(tmp_path / 'data' / 'code-sessions')
    yield service, item, source
    service.call(service.shutdown()); service.loop.call_soon_threadsafe(service.loop.stop); service.thread.join(5)


@pytest.mark.parametrize('runtime', ['codex', 'claude_cli', 'acp'])
def test_folder_creation_is_git_free_and_metadata_stays_in_app_data(folder, monkeypatch, runtime):
    service, item, source = folder
    def forbidden(*args, **kwargs): pytest.fail('Folder creation must not invoke Git or copy the source')
    monkeypatch.setattr(wt, 'git', forbidden)
    row = service.call(service.create(item.id, runtime, 'plain-folder', command=[sys.executable] if runtime == 'acp' else None))
    assert row['workspace_mode'] == 'folder' and row['workspace_root'] == str(source.resolve())
    session = service.get(row['id'])
    assert session.directory == service.directory / row['id']
    assert (session.directory / 'session.json').is_file()
    assert not (source / '.git').exists() and sorted(p.name for p in source.iterdir()) == ['hello.txt']
    assert not row['rollback_available'] and row['base_head'] == ''
    again = service.call(service.create(item.id, runtime, 'plain-folder', command=[sys.executable] if runtime == 'acp' else None))
    assert again['id'] == row['id']
    with pytest.raises(ValueError, match='different request'):
        service.call(service.create(item.id, runtime, 'plain-folder', workspace_mode='worktree', command=[sys.executable] if runtime == 'acp' else None))
    with pytest.raises(ValueError, match='only for isolated'):
        service.call(service.rollback(row['id']))


def test_codex_folder_turn_edits_the_actual_folder_without_git_checkpoint(folder, monkeypatch):
    service, item, source = folder
    fake(monkeypatch, 'codex')
    monkeypatch.setattr(wt, 'snapshot', lambda *_: pytest.fail('Folder tasks must not require a Git checkpoint'))
    row = service.call(service.create(item.id, 'codex', 'folder-task')); sid = row['id']
    service.call(service.connect(sid)); service.call(service.prompt(sid, 'fixture task'))
    waiting = wait_for(service, sid, lambda r: bool(r['approvals']))
    service.call(service.decide(sid, waiting['approvals'][0]['id'], True))
    done = wait_for(service, sid, lambda r: r['state'] == 'completed' and not r['turn_active'])
    assert (source / 'hello.txt').read_text() == 'agent edit'
    assert not (source / '.git').exists() and not done['rollback_available']
    assert not (service.directory / sid / 'checkpoint.json').exists()
    service.call(service.stop(sid)); reopened = service.call(service.connect(sid))
    assert reopened['native_id'] == done['native_id'] and reopened['model'] == done['model']


def test_claude_folder_execution_uses_the_selected_directory(folder, signed_in):
    service, item, source = folder
    row = service.call(service.create(item.id, 'claude_cli', 'claude-folder')); sid = row['id']
    service.call(service.connect(sid)); service.call(service.prompt(sid, 'write-folder-fixture'))
    done = wait_for(service, sid, lambda r: r['state'] == 'completed' and not r['turn_active'])
    assert (source / 'folder-result.txt').read_text() == 'fixture edit'
    assert not (source / '.git').exists() and done['native_id'] == 'fake-session-1'


def test_folder_root_writer_lock_is_shared_with_workbench_and_other_sessions(folder, monkeypatch):
    service, item, source = folder; fake(monkeypatch, 'codex')
    one = service.call(service.create(item.id, 'codex', 'one'))['id']
    two = service.call(service.create(item.id, 'codex', 'two'))['id']
    service.call(service.connect(one))
    with pytest.raises(ValueError, match='in use'):
        service.call(service.connect(two))
    bench = Workbench(service)
    job = OwnedJob(bench, bench.context(item.id))
    with pytest.raises(ValueError, match='another writer'):
        bench.acquire(job)
    service.call(service.stop(one)); service.call(service.connect(two))
    assert service.get(two).folder_lease
    service.call(service.stop(two)); bench.acquire(job); bench.release(job)


def test_git_can_be_added_later_without_replacing_the_folder_session(folder):
    service, item, source = folder
    row = service.call(service.create(item.id, 'codex', 'git-later')); sid = row['id']
    bench = Workbench(service); context = bench.context(item.id, sid)
    assert not service.call(bench.summary(context))['git']['is_git']
    assert service.call(bench.file(context, 'hello.txt'))['text'] == 'owner local content'
    # Represents the owner's agent/terminal initializing Git on request, not automatic setup.
    wt.git(source, 'init'); wt.git(source, 'config', 'user.name', 'Fixture'); wt.git(source, 'config', 'user.email', 'fixture@example.invalid')
    initialized = service.call(bench.summary(context))
    assert initialized['git']['is_git'] and not initialized['git']['has_commits']
    assert any(r['path'] == 'hello.txt' for r in initialized['git']['changes'])
    review = service.call(bench.review(context, 'commit', ['hello.txt'], 'First requested commit', ''))
    applied = service.call(bench.apply(context, review['review_id']))
    assert applied['summary']['git']['head']
    review = service.call(bench.review(context, 'create_branch', [], '', 'feature/folder'))
    service.call(bench.apply(context, review['review_id']))
    service.get(sid).workspace_checked = 0
    assert service.call(service.poll(sid, 0))['branch'] == 'feature/folder'
    assert service.get(sid).root == source.resolve() and row['id'] == sid


def test_explicit_worktree_never_silently_falls_back_and_unknown_modes_fail(folder):
    service, item, source = folder
    with pytest.raises(ValueError, match='not a Git repository'):
        service.call(service.create(item.id, 'codex', 'isolation', workspace_mode='worktree'))
    with pytest.raises(ValueError, match='Choose Project folder'):
        service.call(service.create(item.id, 'codex', 'bad-mode', workspace_mode='auto'))
    assert (source / 'hello.txt').read_text() == 'owner local content' and not (source / '.git').exists()


def test_folder_binding_and_mode_cannot_be_changed_in_saved_metadata(folder, monkeypatch):
    service, item, source = folder; fake(monkeypatch, 'codex')
    row = service.call(service.create(item.id, 'codex', 'binding')); sid = row['id']
    metadata = service.directory / sid / 'session.json'
    saved = json.loads(metadata.read_text()); saved['workspace_mode'] = 'worktree'
    wt.atomic_json(metadata, saved)
    with pytest.raises(ValueError, match='metadata changed'):
        service.call(service.connect(sid))
    assert (source / 'hello.txt').read_text() == 'owner local content'


def test_owner_api_defaults_to_folder_and_only_blocks_explicit_worktree(folder, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routes.echo_code import router
    service, item, source = folder
    monkeypatch.setattr(cs, 'manager', lambda: service)
    app = FastAPI(); app.include_router(router); client = TestClient(app)
    workspace = client.get(f'/echo-code/projects/{item.id}').json()
    assert workspace['external_blocker'] == '' and not workspace['worktree_available'] and workspace['can_start_native']
    body = {'runtime_id': 'codex', 'idempotency_key': 'api-folder-request', 'acknowledge_execution': True}
    result = client.post(f'/echo-code/projects/{item.id}/external-sessions', json=body)
    assert result.status_code == 200 and result.json()['workspace_mode'] == 'folder'
    body.update(idempotency_key='api-worktree-request', workspace_mode='worktree')
    result = client.post(f'/echo-code/projects/{item.id}/external-sessions', json=body)
    assert result.status_code == 400 and 'not a Git repository' in result.json()['detail']
    assert not (source / '.git').exists()


def test_git_not_installed_does_not_block_folder_poll_or_files(folder, monkeypatch):
    service, item, source = folder
    def missing(*args, **kwargs): raise OSError('Git unavailable')
    monkeypatch.setattr(wt, 'git', missing)
    row = service.call(service.create(item.id, 'codex', 'no-git-program'))
    assert service.call(service.poll(row['id'], 0))['workspace_mode'] == 'folder'
    bench = Workbench(service); context = bench.context(item.id, row['id'])
    assert not service.call(bench.summary(context))['git']['is_git']
    assert service.call(bench.files(context))['items'][0]['name'] == 'hello.txt'


def test_failed_connection_releases_folder_writer_lock(folder, monkeypatch):
    from agent import code_runtimes
    service, item, source = folder
    row = service.call(service.create(item.id, 'codex', 'connect-failure'))
    def fail(*args, **kwargs): raise RuntimeError('Fixture connection failure')
    monkeypatch.setattr(code_runtimes, 'RpcProcess', fail)
    with pytest.raises(RuntimeError, match='Fixture connection'):
        service.call(service.connect(row['id']))
    assert not service.get(row['id']).lease and not service.get(row['id']).folder_lease
    fake(monkeypatch, 'codex')
    assert service.call(service.connect(row['id']))['connected']


def test_legacy_worktree_rows_remain_isolated_and_retryable(folder):
    service, item, source = folder
    wt.git(source, 'init'); wt.git(source, 'add', 'hello.txt')
    wt.git(source, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture')
    row = service.call(service.create(item.id, 'codex', 'legacy-worktree', workspace_mode='worktree'))
    metadata = service.directory / row['id'] / 'session.json'
    saved = json.loads(metadata.read_text()); saved.pop('workspace_mode'); saved['creation_context'].pop('workspace_mode')
    wt.atomic_json(metadata, saved); service.sessions.pop(row['id']); service.call(service._load())
    restored = service.get(row['id'])
    assert restored.workspace_mode == 'worktree' and restored.root != source.resolve()
    assert service.call(service.create(item.id, 'codex', 'legacy-worktree', workspace_mode='worktree'))['id'] == row['id']
