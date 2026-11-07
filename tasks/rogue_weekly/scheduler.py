"""Return control to SRC after each confirmed roguelike settlement."""
from module.logger import logger


def leave_lobby(ui):
    """Leave a confirmed settlement lobby through visible game controls."""
    from module.base.timer import Timer
    from tasks.base.page import Page, page_main
    from tasks.divergent_universe.operator import DivergentOperator
    operator = DivergentOperator(ui.device, ui.config)
    timeout = Timer(45, count=3).start()
    while not timeout.reached():
        operator.snapshot()
        if ui.ui_page_appear(page_main):
            return
        if any(page.check_button is not None and ui.ui_page_appear(page) for page in Page.iter_pages()):
            ui.ui_goto(page_main)
            return
        if operator.back() or ui.ui_additional():
            continue
    raise RuntimeError('肉鸽结算后未能返回主界面')


def finish_batch(config, runs, repeat_daily, completed=1):
    """Persist a finite batch across scheduler switches and process restarts."""
    count = config.RogueBatch_CompletedRuns + completed
    with config.multi_set():
        if runs == 0 or count < runs:
            config.RogueBatch_CompletedRuns = count if runs else 0
            config.task_delay(minute=0)
            logger.info('Roguelike settlement complete, return to scheduler')
            return False
        config.RogueBatch_CompletedRuns = 0
        config.task_delay(server_update=True)
        if not repeat_daily:
            config.Scheduler_Enable = False
    return True
