# platform/windows/media.py
# Pause and resume the players Windows knows about (Spotify, browser tabs,
# media apps) through the System Media Transport Controls, the same sessions
# the volume flyout shows. Unlike the play/pause key, this can tell what is
# actually playing, so nothing that was stopped ever gets started.

import asyncio
import logging

logger = logging.getLogger(__name__)

# GlobalSystemMediaTransportControlsSessionPlaybackStatus values.
_PLAYING = 4
_PAUSED = 5


async def _sessions():
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as SessionManager)
    manager = await SessionManager.request_async()
    return list(manager.get_sessions())


def _status(session) -> int:
    return int(session.get_playback_info().playback_status)


async def _pause_playing() -> list:
    paused = []
    for session in await _sessions():
        if _status(session) == _PLAYING:
            try:
                if await session.try_pause_async():
                    paused.append(session.source_app_user_model_id)
            except OSError as e:
                logger.debug(f"Could not pause {session.source_app_user_model_id}: {e}")
    return paused


# Only sessions that are still paused: one the user stopped, closed or
# switched to another track in the meantime is left as it is.
async def _resume(app_ids: list) -> list:
    resumed = []
    wanted = set(app_ids)
    for session in await _sessions():
        app_id = session.source_app_user_model_id
        if app_id in wanted and _status(session) == _PAUSED:
            try:
                if await session.try_play_async():
                    resumed.append(app_id)
            except OSError as e:
                logger.debug(f"Could not resume {app_id}: {e}")
    return resumed


# Pause everything that is playing. Returns the app ids it paused.
def pause_playing() -> list:
    return asyncio.run(_pause_playing())


# Resume the given app ids, if they are still paused. Returns those resumed.
def resume(app_ids: list) -> list:
    if not app_ids:
        return []
    return asyncio.run(_resume(app_ids))
