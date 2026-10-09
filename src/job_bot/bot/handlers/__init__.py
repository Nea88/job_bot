from aiogram import Router

from job_bot.bot.handlers import admin, filters, interview, start


def build_router() -> Router:
    router = Router()
    router.include_routers(start.router, filters.router, interview.router, admin.router)
    return router
