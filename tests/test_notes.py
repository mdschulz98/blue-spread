import uuid
from typing import Any

from httpx import AsyncClient

from tests.conftest import MakeUser, TeamFixture, create_note, create_team, patch_note


async def test_create_and_read_note(client: AsyncClient, team: TeamFixture) -> None:
    response = await client.post(
        "/api/v1/notes",
        json={
            "team_id": str(team.id),
            "title": "Runbook",
            "content": "# Deploy\n\nRun **make deploy**.",
            "tags": ["Ops", "ops", "deploy"],
        },
        headers=team.editor.headers,
    )
    assert response.status_code == 201
    note = response.json()
    assert response.headers["Location"] == f"/api/v1/notes/{note['id']}"
    assert response.headers["ETag"] == '"1"'
    assert note["version"] == 1
    assert note["tags"] == ["deploy", "ops"]
    assert note["excerpt"] == "Deploy Run make deploy."
    assert note["created_by"] == {"id": str(team.editor.id), "display_name": "Bob"}
    assert note["deleted_at"] is None

    fetched = await client.get(f"/api/v1/notes/{note['id']}", headers=team.viewer.headers)
    assert fetched.status_code == 200
    assert fetched.headers["ETag"] == '"1"'
    assert fetched.json()["content"] == "# Deploy\n\nRun **make deploy**."


async def test_role_requirements_for_notes(client: AsyncClient, team: TeamFixture) -> None:
    viewer_create = await client.post(
        "/api/v1/notes",
        json={"team_id": str(team.id), "title": "Nope"},
        headers=team.viewer.headers,
    )
    assert viewer_create.status_code == 403

    note = await create_note(client, team.editor, team.id)
    viewer_patch = await patch_note(client, team.viewer, note["id"], 1, {"title": "x"})
    assert viewer_patch.status_code == 403
    viewer_delete = await client.delete(
        f"/api/v1/notes/{note['id']}", headers={**team.viewer.headers, "If-Match": '"1"'}
    )
    assert viewer_delete.status_code == 403

    admin_patch = await patch_note(client, team.admin, note["id"], 1, {"title": "Admin edit"})
    assert admin_patch.status_code == 200


async def test_non_member_gets_404_for_notes(client: AsyncClient, team: TeamFixture) -> None:
    note = await create_note(client, team.editor, team.id)
    outsider = team.outsider
    note_url = f"/api/v1/notes/{note['id']}"
    assert (await client.get(note_url, headers=outsider.headers)).status_code == 404
    assert (await patch_note(client, outsider, note["id"], 1, {"title": "x"})).status_code == 404
    assert (
        await client.delete(note_url, headers={**outsider.headers, "If-Match": '"1"'})
    ).status_code == 404
    assert (await client.get(f"{note_url}/revisions", headers=outsider.headers)).status_code == 404
    assert (await client.post(f"{note_url}/restore", headers=outsider.headers)).status_code == 404
    create = await client.post(
        "/api/v1/notes",
        json={"team_id": str(team.id), "title": "Sneaky"},
        headers=outsider.headers,
    )
    assert create.status_code == 404
    listing = await client.get(
        "/api/v1/notes", params={"team_id": str(team.id)}, headers=outsider.headers
    )
    assert listing.status_code == 404
    unscoped = await client.get("/api/v1/notes", headers=outsider.headers)
    assert unscoped.json()["total"] == 0
    assert (
        await client.get(f"/api/v1/notes/{uuid.uuid4()}", headers=outsider.headers)
    ).status_code == 404


async def test_soft_delete_and_restore(client: AsyncClient, team: TeamFixture) -> None:
    note = await create_note(client, team.editor, team.id, title="Temp")
    url = f"/api/v1/notes/{note['id']}"

    missing = await client.delete(url, headers=team.editor.headers)
    assert missing.status_code == 428
    assert missing.json()["error"]["code"] == "precondition_required"

    deleted = await client.delete(url, headers={**team.editor.headers, "If-Match": '"1"'})
    assert deleted.status_code == 204

    assert (await client.get(url, headers=team.editor.headers)).status_code == 404
    # include_deleted works for editors+, not for viewers.
    with_deleted = await client.get(
        url, params={"include_deleted": "true"}, headers=team.editor.headers
    )
    assert with_deleted.status_code == 200
    assert with_deleted.json()["deleted_by"]["id"] == str(team.editor.id)
    viewer_view = await client.get(
        url, params={"include_deleted": "true"}, headers=team.viewer.headers
    )
    assert viewer_view.status_code == 404

    # Edits to deleted notes are not allowed.
    assert (await patch_note(client, team.editor, note["id"], 1, {"title": "x"})).status_code == 404

    assert (await client.post(f"{url}/restore", headers=team.viewer.headers)).status_code == 404
    restored = await client.post(f"{url}/restore", headers=team.editor.headers)
    assert restored.status_code == 200
    assert restored.json()["deleted_at"] is None
    assert restored.headers["ETag"] == '"1"'
    again = await client.post(f"{url}/restore", headers=team.editor.headers)
    assert again.status_code == 409


async def test_stale_delete_is_412(client: AsyncClient, team: TeamFixture) -> None:
    note = await create_note(client, team.editor, team.id)
    await patch_note(client, team.editor, note["id"], 1, {"title": "v2"})
    for etag in ('"1"', '"7"'):
        response = await client.delete(
            f"/api/v1/notes/{note['id']}", headers={**team.editor.headers, "If-Match": etag}
        )
        assert response.status_code == 412
        assert response.json()["error"]["details"] == {"current_version": 2}
    malformed = await client.delete(
        f"/api/v1/notes/{note['id']}", headers={**team.editor.headers, "If-Match": "*"}
    )
    assert malformed.status_code == 400


# --- listing -----------------------------------------------------------------------------------


async def test_list_returns_summaries_without_content(
    client: AsyncClient, team: TeamFixture
) -> None:
    long_content = "Secret body. " * 1000
    await create_note(client, team.editor, team.id, title="Big", content=long_content)
    response = await client.get("/api/v1/notes", headers=team.viewer.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert (body["limit"], body["offset"]) == (20, 0)
    item = body["items"][0]
    assert set(item) == {
        "id",
        "team_id",
        "title",
        "excerpt",
        "tags",
        "version",
        "updated_at",
        "updated_by",
        "deleted_at",
    }
    assert "content" not in item
    assert len(item["excerpt"]) <= 200
    assert item["updated_by"]["display_name"] == "Bob"


async def test_list_spans_teams_and_filters_by_team(
    client: AsyncClient, team: TeamFixture, make_user: MakeUser
) -> None:
    other_team = await create_team(client, team.editor, "Other")
    await create_note(client, team.editor, team.id, title="In platform")
    await create_note(client, team.editor, other_team, title="In other")

    everything = await client.get("/api/v1/notes", headers=team.editor.headers)
    assert {n["title"] for n in everything.json()["items"]} == {"In platform", "In other"}

    scoped = await client.get(
        "/api/v1/notes", params={"team_id": str(other_team)}, headers=team.editor.headers
    )
    assert [n["title"] for n in scoped.json()["items"]] == ["In other"]

    viewer_sees = await client.get("/api/v1/notes", headers=team.viewer.headers)
    assert [n["title"] for n in viewer_sees.json()["items"]] == ["In platform"]

    root = await make_user(superuser=True)
    assert (await client.get("/api/v1/notes", headers=root.headers)).json()["total"] == 2


async def test_tag_filter_requires_all_tags(client: AsyncClient, team: TeamFixture) -> None:
    await create_note(client, team.editor, team.id, title="A", tags=["x", "y"])
    await create_note(client, team.editor, team.id, title="B", tags=["x"])
    await create_note(client, team.editor, team.id, title="C", tags=["y", "z"])

    async def titles(*tags: str) -> list[str]:
        response = await client.get(
            "/api/v1/notes",
            params=[*(("tag", t) for t in tags), ("sort", "title")],
            headers=team.viewer.headers,
        )
        return [n["title"] for n in response.json()["items"]]

    assert await titles("x") == ["A", "B"]
    assert await titles("X", "y") == ["A"]
    assert await titles("x", "z") == []


async def test_full_text_search_ranks_title_matches_first(
    client: AsyncClient, team: TeamFixture
) -> None:
    await create_note(client, team.editor, team.id, title="Shopping", content="Buy kubernetes book")
    await create_note(client, team.editor, team.id, title="Kubernetes upgrade", content="Steps")
    await create_note(client, team.editor, team.id, title="Lunch", content="Pizza")
    await create_note(client, team.editor, team.id, title="Clusters", content="no k8s mention")

    response = await client.get(
        "/api/v1/notes", params={"q": "kubernetes"}, headers=team.viewer.headers
    )
    assert [n["title"] for n in response.json()["items"]] == ["Kubernetes upgrade", "Shopping"]

    stemmed = await client.get(
        "/api/v1/notes", params={"q": "upgrading"}, headers=team.viewer.headers
    )
    assert [n["title"] for n in stemmed.json()["items"]] == ["Kubernetes upgrade"]

    websearch = await client.get(
        "/api/v1/notes", params={"q": "kubernetes -book"}, headers=team.viewer.headers
    )
    assert [n["title"] for n in websearch.json()["items"]] == ["Kubernetes upgrade"]

    phrase = await client.get(
        "/api/v1/notes", params={"q": '"buy kubernetes"'}, headers=team.viewer.headers
    )
    assert [n["title"] for n in phrase.json()["items"]] == ["Shopping"]


async def test_sorting_and_pagination(client: AsyncClient, team: TeamFixture) -> None:
    for title in ["delta", "Alpha", "charlie", "bravo", "echo"]:
        await create_note(client, team.editor, team.id, title=title)

    async def page(**params: Any) -> Any:
        response = await client.get("/api/v1/notes", params=params, headers=team.viewer.headers)
        assert response.status_code == 200, response.text
        return response.json()

    default = await page()
    assert [n["title"] for n in default["items"]] == ["echo", "bravo", "charlie", "Alpha", "delta"]

    first = await page(sort="title", limit=2)
    second = await page(sort="title", limit=2, offset=2)
    third = await page(sort="title", limit=2, offset=4)
    titles = [n["title"] for p in (first, second, third) for n in p["items"]]
    assert titles == ["Alpha", "bravo", "charlie", "delta", "echo"]
    assert first["total"] == 5
    assert third["offset"] == 4

    desc = await page(sort="-title", limit=1)
    assert desc["items"][0]["title"] == "echo"
    created = await page(sort="created_at", limit=1)
    assert created["items"][0]["title"] == "delta"

    for bad in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"sort": "content"}):
        response = await client.get("/api/v1/notes", params=bad, headers=team.viewer.headers)
        assert response.status_code == 422, bad


async def test_include_deleted_in_listing(client: AsyncClient, team: TeamFixture) -> None:
    note = await create_note(client, team.editor, team.id, title="Gone")
    await create_note(client, team.editor, team.id, title="Here")
    await client.delete(
        f"/api/v1/notes/{note['id']}", headers={**team.editor.headers, "If-Match": '"1"'}
    )

    default = await client.get("/api/v1/notes", headers=team.editor.headers)
    assert [n["title"] for n in default.json()["items"]] == ["Here"]

    editor = await client.get(
        "/api/v1/notes", params={"include_deleted": "true"}, headers=team.editor.headers
    )
    items = {n["title"]: n for n in editor.json()["items"]}
    assert set(items) == {"Here", "Gone"}
    assert items["Gone"]["deleted_at"] is not None

    # Viewers never see deleted notes: 403 when scoped to the team, filtered when unscoped.
    scoped = await client.get(
        "/api/v1/notes",
        params={"include_deleted": "true", "team_id": str(team.id)},
        headers=team.viewer.headers,
    )
    assert scoped.status_code == 403
    unscoped = await client.get(
        "/api/v1/notes", params={"include_deleted": "true"}, headers=team.viewer.headers
    )
    assert [n["title"] for n in unscoped.json()["items"]] == ["Here"]


# --- revisions ---------------------------------------------------------------------------------


async def test_revisions_written_for_every_version(client: AsyncClient, team: TeamFixture) -> None:
    note = await create_note(client, team.editor, team.id, title="v1", content="one\n")
    r2 = await patch_note(client, team.editor, note["id"], 1, {"title": "v2"})
    r3 = await patch_note(client, team.admin, note["id"], 2, {"content": "three\n"})
    assert (r2.status_code, r3.status_code) == (200, 200)
    assert r3.headers["ETag"] == '"3"'

    url = f"/api/v1/notes/{note['id']}/revisions"
    listing = await client.get(url, headers=team.viewer.headers)
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 3
    assert [(r["version"], r["title"], r["author"]["display_name"]) for r in body["items"]] == [
        (3, "v2", "Alice"),
        (2, "v2", "Bob"),
        (1, "v1", "Bob"),
    ]
    assert all("content" not in r for r in body["items"])

    v1 = await client.get(f"{url}/1", headers=team.viewer.headers)
    assert v1.status_code == 200
    assert (v1.json()["title"], v1.json()["content"]) == ("v1", "one\n")
    assert (await client.get(f"{url}/9", headers=team.viewer.headers)).status_code == 404


async def test_noop_update_does_not_create_version(client: AsyncClient, team: TeamFixture) -> None:
    note = await create_note(client, team.editor, team.id, title="Same", tags=["a"])
    response = await patch_note(client, team.editor, note["id"], 1, {"title": "Same"})
    assert response.status_code == 200
    assert response.json()["version"] == 1
    revisions = await client.get(
        f"/api/v1/notes/{note['id']}/revisions", headers=team.editor.headers
    )
    assert revisions.json()["total"] == 1


async def test_patch_validation(client: AsyncClient, team: TeamFixture) -> None:
    note = await create_note(client, team.editor, team.id)
    bodies: list[dict[str, Any]] = [{}, {"title": None}, {"title": ""}, {"tags": ["a,b"]}, {"x": 1}]
    for body in bodies:
        response = await patch_note(client, team.editor, note["id"], 1, body)
        assert response.status_code == 422, body
