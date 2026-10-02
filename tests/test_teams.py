import uuid

from httpx import AsyncClient

from tests.conftest import MakeUser, TeamFixture, add_member, create_note, create_team


async def test_creator_becomes_admin_and_lists_only_own_teams(
    client: AsyncClient, make_user: MakeUser
) -> None:
    alice = await make_user("Alice")
    bob = await make_user("Bob")
    response = await client.post(
        "/api/v1/teams", json={"name": "Docs", "description": "d"}, headers=alice.headers
    )
    assert response.status_code == 201
    team = response.json()
    assert team["my_role"] == "admin"
    assert response.headers["Location"] == f"/api/v1/teams/{team['id']}"
    await create_team(client, bob, "Bob's team")

    alice_teams = (await client.get("/api/v1/teams", headers=alice.headers)).json()
    assert [t["name"] for t in alice_teams] == ["Docs"]


async def test_team_names_are_unique_case_insensitively(
    client: AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user()
    await create_team(client, user, "Unique")
    response = await client.post("/api/v1/teams", json={"name": "UNIQUE"}, headers=user.headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "team_name_taken"


async def test_non_member_gets_404_not_403(client: AsyncClient, team: TeamFixture) -> None:
    headers = team.outsider.headers
    for method, path in [
        ("GET", f"/api/v1/teams/{team.id}"),
        ("PATCH", f"/api/v1/teams/{team.id}"),
        ("DELETE", f"/api/v1/teams/{team.id}"),
        ("GET", f"/api/v1/teams/{team.id}/members"),
    ]:
        response = await client.request(method, path, headers=headers, json={"name": "x"})
        assert response.status_code == 404, (method, path)
    missing = await client.get(f"/api/v1/teams/{uuid.uuid4()}", headers=headers)
    assert missing.status_code == 404


async def test_members_can_read_but_only_admin_can_manage(
    client: AsyncClient, team: TeamFixture
) -> None:
    for user in (team.viewer, team.editor):
        assert (await client.get(f"/api/v1/teams/{team.id}", headers=user.headers)).json()[
            "my_role"
        ] in ("viewer", "editor")
        members = await client.get(f"/api/v1/teams/{team.id}/members", headers=user.headers)
        assert members.status_code == 200
        assert len(members.json()) == 3

        patch = await client.patch(
            f"/api/v1/teams/{team.id}", json={"description": "x"}, headers=user.headers
        )
        assert patch.status_code == 403
        add = await client.post(
            f"/api/v1/teams/{team.id}/members",
            json={"email": team.outsider.email, "role": "viewer"},
            headers=user.headers,
        )
        assert add.status_code == 403

    patch = await client.patch(
        f"/api/v1/teams/{team.id}", json={"description": "Updated"}, headers=team.admin.headers
    )
    assert patch.status_code == 200
    assert patch.json()["description"] == "Updated"


async def test_member_management(client: AsyncClient, team: TeamFixture) -> None:
    base = f"/api/v1/teams/{team.id}/members"
    duplicate = await client.post(
        base, json={"email": team.viewer.email, "role": "viewer"}, headers=team.admin.headers
    )
    assert duplicate.status_code == 409
    unknown = await client.post(
        base, json={"email": "ghost@example.com"}, headers=team.admin.headers
    )
    assert unknown.status_code == 404

    promote = await client.patch(
        f"{base}/{team.viewer.id}", json={"role": "editor"}, headers=team.admin.headers
    )
    assert promote.status_code == 200
    assert promote.json()["role"] == "editor"

    remove = await client.delete(f"{base}/{team.viewer.id}", headers=team.admin.headers)
    assert remove.status_code == 204
    after = await client.get(f"/api/v1/teams/{team.id}", headers=team.viewer.headers)
    assert after.status_code == 404


async def test_member_can_leave_but_not_remove_others(
    client: AsyncClient, team: TeamFixture
) -> None:
    base = f"/api/v1/teams/{team.id}/members"
    other = await client.delete(f"{base}/{team.editor.id}", headers=team.viewer.headers)
    assert other.status_code == 403
    leave = await client.delete(f"{base}/{team.viewer.id}", headers=team.viewer.headers)
    assert leave.status_code == 204


async def test_last_admin_cannot_be_demoted_or_removed(
    client: AsyncClient, team: TeamFixture
) -> None:
    base = f"/api/v1/teams/{team.id}/members"
    demote = await client.patch(
        f"{base}/{team.admin.id}", json={"role": "editor"}, headers=team.admin.headers
    )
    assert demote.status_code == 409
    assert demote.json()["error"]["code"] == "last_admin"
    leave = await client.delete(f"{base}/{team.admin.id}", headers=team.admin.headers)
    assert leave.status_code == 409

    # With a second admin, the first may step down.
    await client.patch(
        f"{base}/{team.editor.id}", json={"role": "admin"}, headers=team.admin.headers
    )
    demote = await client.patch(
        f"{base}/{team.admin.id}", json={"role": "viewer"}, headers=team.admin.headers
    )
    assert demote.status_code == 200


async def test_delete_team_only_without_live_notes(client: AsyncClient, team: TeamFixture) -> None:
    note = await create_note(client, team.editor, team.id, title="Blocker")
    blocked = await client.delete(f"/api/v1/teams/{team.id}", headers=team.admin.headers)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "team_not_empty"

    deleted_note = await client.delete(
        f"/api/v1/notes/{note['id']}", headers={**team.editor.headers, "If-Match": '"1"'}
    )
    assert deleted_note.status_code == 204

    assert (
        await client.delete(f"/api/v1/teams/{team.id}", headers=team.editor.headers)
    ).status_code == 403
    response = await client.delete(f"/api/v1/teams/{team.id}", headers=team.admin.headers)
    assert response.status_code == 204
    assert (
        await client.get(f"/api/v1/teams/{team.id}", headers=team.admin.headers)
    ).status_code == 404


async def test_superuser_sees_and_manages_all_teams(
    client: AsyncClient, team: TeamFixture, make_user: MakeUser
) -> None:
    root = await make_user("Root", superuser=True)
    teams = (await client.get("/api/v1/teams", headers=root.headers)).json()
    assert [(t["name"], t["my_role"]) for t in teams] == [("Platform", None)]
    response = await client.get(f"/api/v1/teams/{team.id}/members", headers=root.headers)
    assert response.status_code == 200
    newcomer = await make_user("Newcomer")
    await add_member(client, team.id, root, newcomer, "viewer")
