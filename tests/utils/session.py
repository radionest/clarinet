"""Test adapters around AsyncSession."""

from sqlalchemy.ext.asyncio import AsyncSession


class PassThroughSession:
    """Wrap an existing AsyncSession to be used as ``async with``.

    Production code does ``async with factory() as session``; tests
    typically inject a session via fixture and want to skip close/commit
    so the fixture keeps lifecycle control. Use as::

        mock_dbm.async_session_factory = lambda: PassThroughSession(test_session)
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc: object) -> None:
        return None
