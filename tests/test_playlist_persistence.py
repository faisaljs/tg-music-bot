"""Playlist writes preserve other requests' changes without accessing MongoDB."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

import database


class PlaylistCollection:
    """Model the atomic collection operations used by the playlist API."""

    def __init__(self, tracks):
        self.document = {
            "_id": "user-document",
            "user_id": 123,
            "playlists": [{"id": "playlist", "name": "Music", "created_at": 1, "tracks": tracks}],
        }
        self.removal_started = asyncio.Event()
        self.resume_removal = asyncio.Event()
        self.interleave = True

    async def find_one(self, query):
        if query.get("user_id") == self.document["user_id"]:
            return deepcopy(self.document)
        return None

    async def update_one(self, query, update, **kwargs):
        # Suspend only the first removal write, letting an addition commit after
        # its preceding read but before the removal's atomic update executes.
        if self.interleave and ("$set" in update or "$pull" in update):
            self.interleave = False
            self.removal_started.set()
            await self.resume_removal.wait()

        if query.get("user_id") != self.document["user_id"]:
            return SimpleNamespace(modified_count=0)
        criterion = query.get("playlists", {}).get("$elemMatch", {})
        playlist_id = criterion.get("id", query.get("playlists.id"))
        playlist = next((pl for pl in self.document["playlists"] if pl["id"] == playlist_id), None)
        if playlist is None:
            return SimpleNamespace(modified_count=0)
        tracks = playlist["tracks"]
        if "tracks" in criterion and tracks != criterion["tracks"]:
            return SimpleNamespace(modified_count=0)
        if "tracks.id" in criterion and not any(t.get("id") == criterion["tracks.id"] for t in tracks):
            return SimpleNamespace(modified_count=0)

        previous = deepcopy(tracks)
        if "$push" in update:
            tracks.append(deepcopy(update["$push"]["playlists.$.tracks"]))
        elif "$pull" in update:
            target_id = update["$pull"]["playlists.$.tracks"]["id"]
            playlist["tracks"] = [t for t in tracks if t.get("id") != target_id]
        elif "$set" in update:
            playlist["tracks"] = deepcopy(update["$set"]["playlists.$.tracks"])
        else:
            raise AssertionError(f"Unexpected playlist operation: {update}")
        return SimpleNamespace(modified_count=int(previous != playlist["tracks"]))


def track(title, *, legacy=False):
    result = {
        "title": title,
        "duration": "3:00",
        "yt_link": f"https://www.youtube.com/watch?v={title}",
        "video_id": title,
        "mode": "audio",
    }
    if not legacy:
        result["id"] = title
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True], ids=["track-ids", "legacy-tracks"])
async def test_removal_preserves_concurrently_added_track(monkeypatch, legacy):
    collection = PlaylistCollection([track("A", legacy=legacy), track("B", legacy=legacy)])
    monkeypatch.setattr(database, "user_playlists", collection)

    removal = asyncio.create_task(database.remove_track_from_playlist(123, "playlist", 0))
    try:
        await asyncio.wait_for(collection.removal_started.wait(), timeout=1)
        assert await database.add_track_to_playlist(123, "playlist", track("C")) == (True, "ADDED")
    finally:
        collection.resume_removal.set()
    assert await removal is True

    playlist = await database.get_playlist(123, "playlist")
    assert [t["title"] for t in playlist["tracks"]] == ["B", "C"]


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True], ids=["track-ids", "legacy-tracks"])
async def test_concurrent_removals_keep_the_original_selection(monkeypatch, legacy):
    collection = PlaylistCollection([track(t, legacy=legacy) for t in ("A", "B", "C")])
    monkeypatch.setattr(database, "user_playlists", collection)

    removal = asyncio.create_task(database.remove_track_from_playlist(123, "playlist", 1))
    try:
        await asyncio.wait_for(collection.removal_started.wait(), timeout=1)
        assert await database.remove_track_from_playlist(123, "playlist", 0) is True
    finally:
        collection.resume_removal.set()
    assert await removal is True

    playlist = await database.get_playlist(123, "playlist")
    assert [t["title"] for t in playlist["tracks"]] == ["C"]


@pytest.mark.asyncio
async def test_conflicting_removal_of_identical_legacy_tracks_fails_safely(monkeypatch):
    collection = PlaylistCollection([track("A", legacy=True), track("A", legacy=True), track("B", legacy=True)])
    monkeypatch.setattr(database, "user_playlists", collection)

    removal = asyncio.create_task(database.remove_track_from_playlist(123, "playlist", 1))
    try:
        await asyncio.wait_for(collection.removal_started.wait(), timeout=1)
        assert await database.add_track_to_playlist(123, "playlist", track("C")) == (True, "ADDED")
    finally:
        collection.resume_removal.set()
    assert await removal is False

    playlist = await database.get_playlist(123, "playlist")
    assert [t["title"] for t in playlist["tracks"]] == ["A", "A", "B", "C"]
