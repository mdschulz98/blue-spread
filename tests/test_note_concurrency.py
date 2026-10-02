"""API tests of the If-Match / three-way merge contract (README: "Concurrent edits")."""

import asyncio
from typing import Any

from httpx import AsyncClient

from tests.conftest import TeamFixture, TestUser, create_note, patch_note

BASE = "# Plan\nline 2\nline 3\nline 4\nline 5\n"


async def _get(client: AsyncClient, team: TeamFixture, note_id: object) -> dict[str, Any]:
    response = await client.get(f"/api/v1/notes/{note_id}", headers=team.viewer.headers)
    body: dict[str, Any] = response.json()
    return body


async def test_missing_if_match_is_428(client: AsyncClient, team: TeamFixture) -> None:
    note = await create_note(client, team.editor, team.id)
    response = await patch_note(client, team.editor, note["id"], None, {"title": "x"})
    assert response.status_code == 428
    assert response.json()["error"]["code"] == "precondition_required"


async def test_fast_forward_update(client: AsyncClient, team: TeamFixture) -> None:
    note = await create_note(client, team.editor, team.id, title="Old", content=BASE)
    response = await patch_note(client, team.editor, note["id"], 1, {"title": "New"})
    assert response.status_code == 200
    assert response.headers["ETag"] == '"2"'
    assert "X-Note-Merged" not in response.headers
    body = response.json()
    assert (body["title"], body["content"], body["version"]) == ("New", BASE, 2)
    assert body["merged_from_versions"] is None
    assert body["updated_by"]["display_name"] == "Bob"


async def test_weak_and_unquoted_etags_are_accepted(client: AsyncClient, team: TeamFixture) -> None:
    note = await create_note(client, team.editor, team.id)
    for version, etag in [(1, 'W/"1"'), (2, "2")]:
        response = await client.patch(
            f"/api/v1/notes/{note['id']}",
            json={"title": f"t{version}"},
            headers={**team.editor.headers, "If-Match": etag},
        )
        assert response.status_code == 200, etag


async def test_future_or_unknown_base_is_412(client: AsyncClient, team: TeamFixture) -> None:
    note = await create_note(client, team.editor, team.id)
    for version in (2, 99, 0):
        response = await patch_note(client, team.editor, note["id"], version, {"title": "x"})
        assert response.status_code == 412, version
        assert response.json()["error"]["details"]["current_version"] == 1


async def test_clean_content_merge_of_non_overlapping_edits(
    client: AsyncClient, team: TeamFixture
) -> None:
    note = await create_note(client, team.editor, team.id, title="Plan", content=BASE)
    # Alice saves first (v2), editing the top of the document.
    first = await patch_note(
        client, team.admin, note["id"], 1, {"content": BASE.replace("# Plan", "# Plan (draft)")}
    )
    assert first.status_code == 200
    # Bob's edit was also based on v1 and touches the bottom.
    second = await patch_note(
        client, team.editor, note["id"], 1, {"content": BASE.replace("line 5", "line 5 + Bob")}
    )
    assert second.status_code == 200
    assert second.headers["X-Note-Merged"] == "true"
    assert second.headers["ETag"] == '"3"'
    body = second.json()
    assert body["merged_from_versions"] == [1, 2]
    assert body["version"] == 3
    assert body["content"] == "# Plan (draft)\nline 2\nline 3\nline 4\nline 5 + Bob\n"
    assert body["excerpt"].startswith("Plan (draft) line 2")
    assert (await _get(client, team, note["id"]))["content"] == body["content"]


async def test_title_from_one_side_merges_with_content_from_other(
    client: AsyncClient, team: TeamFixture
) -> None:
    note = await create_note(client, team.editor, team.id, title="T", content=BASE)
    await patch_note(client, team.admin, note["id"], 1, {"title": "Renamed by Alice"})
    response = await patch_note(
        client, team.editor, note["id"], 1, {"content": BASE + "appended\n"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Renamed by Alice"
    assert body["content"] == BASE + "appended\n"


async def test_tag_merge_with_concurrent_add_and_remove(
    client: AsyncClient, team: TeamFixture
) -> None:
    note = await create_note(client, team.editor, team.id, tags=["a", "b", "c"])
    first = await patch_note(client, team.admin, note["id"], 1, {"tags": ["a", "b", "c", "alice"]})
    assert first.status_code == 200
    second = await patch_note(client, team.editor, note["id"], 1, {"tags": ["a", "c", "bob"]})
    assert second.status_code == 200
    assert second.headers["X-Note-Merged"] == "true"
    assert second.json()["tags"] == ["a", "alice", "bob", "c"]


async def test_title_conflict_returns_409_and_saves_nothing(
    client: AsyncClient, team: TeamFixture
) -> None:
    note = await create_note(client, team.editor, team.id, title="Original", content=BASE)
    await patch_note(client, team.admin, note["id"], 1, {"title": "Alice's title"})
    response = await patch_note(
        client,
        team.editor,
        note["id"],
        1,
        {"title": "Bob's title", "content": BASE.replace("line 5", "line 5 + Bob")},
    )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "edit_conflict"
    details = error["details"]
    assert details["base_version"] == 1
    assert details["current"]["version"] == 2
    assert details["current"]["title"] == "Alice's title"
    assert details["conflicts"] == [
        {"field": "title", "base": "Original", "theirs": "Alice's title", "yours": "Bob's title"}
    ]
    # The non-conflicting content edit is part of the proposal.
    assert details["merged_proposal"]["content"].endswith("line 5 + Bob\n")

    current = await _get(client, team, note["id"])
    assert (current["version"], current["title"], current["content"]) == (
        2,
        "Alice's title",
        BASE,
    )


async def test_overlapping_content_conflict_returns_markers_and_structure(
    client: AsyncClient, team: TeamFixture
) -> None:
    note = await create_note(client, team.editor, team.id, title="Plan", content=BASE)
    await patch_note(
        client, team.admin, note["id"], 1, {"content": BASE.replace("line 3", "line 3 (Alice)")}
    )
    response = await patch_note(
        client,
        team.editor,
        note["id"],
        1,
        {"content": BASE.replace("line 3", "line 3 (Bob)"), "tags": ["merged-anyway"]},
    )
    assert response.status_code == 409
    details = response.json()["error"]["details"]
    proposal = details["merged_proposal"]["content"]
    assert proposal == (
        "# Plan\nline 2\n"
        "<<<<<<< v2 (Alice)\nline 3 (Alice)\n=======\nline 3 (Bob)\n"
        ">>>>>>> yours (Bob, based on v1)\n"
        "line 4\nline 5\n"
    )
    assert details["merged_proposal"]["tags"] == ["merged-anyway"]
    assert details["conflicts"] == [
        {
            "field": "content",
            "base_lines": {"start": 3, "end": 3},
            "proposal_lines": {"start": 3, "end": 7},
            "base": "line 3\n",
            "theirs": "line 3 (Alice)\n",
            "yours": "line 3 (Bob)\n",
        }
    ]

    # Nothing saved: still v2, no v3 revision.
    current = await _get(client, team, note["id"])
    assert current["version"] == 2
    assert current["tags"] == []
    revisions = await client.get(
        f"/api/v1/notes/{note['id']}/revisions", headers=team.viewer.headers
    )
    assert revisions.json()["total"] == 2

    # The client resolves and re-submits against current.version.
    resolved = await patch_note(
        client,
        team.editor,
        note["id"],
        details["current"]["version"],
        {"content": BASE.replace("line 3", "line 3 (Alice + Bob)")},
    )
    assert resolved.status_code == 200
    assert resolved.json()["version"] == 3


async def test_concurrent_patches_are_serialized_and_merged(
    client: AsyncClient, team: TeamFixture
) -> None:
    note = await create_note(client, team.editor, team.id, content=BASE)
    edits: list[tuple[TestUser, dict[str, Any]]] = [
        (team.admin, {"content": BASE.replace("# Plan", "# Plan A")}),
        (team.editor, {"content": BASE.replace("line 5", "line 5 B")}),
        (team.admin, {"tags": ["parallel"]}),
    ]
    responses = await asyncio.gather(
        *(patch_note(client, user, note["id"], 1, body) for user, body in edits)
    )
    assert sorted(r.status_code for r in responses) == [200, 200, 200]
    assert sorted(r.json()["version"] for r in responses) == [2, 3, 4]
    assert sum(r.headers.get("X-Note-Merged") == "true" for r in responses) == 2

    final = await _get(client, team, note["id"])
    assert final["version"] == 4
    assert final["content"] == BASE.replace("# Plan", "# Plan A").replace("line 5", "line 5 B")
    assert final["tags"] == ["parallel"]
    revisions = await client.get(
        f"/api/v1/notes/{note['id']}/revisions", headers=team.viewer.headers
    )
    assert [r["version"] for r in revisions.json()["items"]] == [4, 3, 2, 1]
