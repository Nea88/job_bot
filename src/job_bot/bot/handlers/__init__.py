from aiogram import Router

from job_bot.bot.handlers import admin, filters, interview, start, telegram_login


def build_router() -> Router:
    router = Router()
    # telegram_login first: its /cancel must win over the generic one while signing in
    router.include_routers(telegram_login.router, start.router, filters.router, interview.router, admin.router)
    return router
