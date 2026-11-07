"""One shared weekly point target, with one settlement per scheduler turn."""
from module.base.timer import Timer
from module.config.utils import get_server_next_monday_update
from module.exception import RequestHumanTakeover
from module.logger import logger
from tasks.dungeon.keywords import KEYWORDS_DUNGEON_NAV, KEYWORDS_DUNGEON_TAB
from tasks.dungeon.ui.nav import DungeonUINav
from tasks.dungeon.ui.state import OcrSimUniPoint
from tasks.rogue_weekly.assets.assets_rogue_weekly_guide import OCR_WEEKLY_POINTS


class RogueWeekly(DungeonUINav):
    def read_points(self):
        self.dungeon_tab_goto(KEYWORDS_DUNGEON_TAB.Simulated_Universe)
        self.dungeon_nav_goto(KEYWORDS_DUNGEON_NAV.Divergent_Universe)
        ocr = OcrSimUniPoint(OCR_WEEKLY_POINTS)
        previous = None
        timeout = Timer(15, count=3).start()
        for _ in self.loop(skip_first=False):
            current, _, total = ocr.ocr_single_line(self.device.image)
            # Require two consistent frames; unknown OCR must never complete a week.
            points = (current, total)
            if 0 <= current <= total and 1000 <= total <= 18000:
                if points == previous:
                    self.config.stored.SimulatedUniverse.set(current, total)
                    return points
                previous = points
            else:
                previous = None
            if timeout.reached():
                raise RequestHumanTakeover('未能确认模拟宇宙共享周积分，已停止，未启动新一局')

    def play_once(self, mode):
        # Reuse the mode's settings while retaining the weekly scheduler identity.
        # override() survives saves/rebinds during a run and is restored afterwards.
        settings = {
            f'{group}_{key}': value
            for group, values in self.config.cross_get(mode, {}).items()
            if group not in ('Scheduler', 'RogueBatch')
            for key, value in values.items()
        }
        if mode == 'Rogue':
            from tasks.rogue.rogue import Rogue
            runner = Rogue(self.config, device=self.device)
            settings.update(RogueWorld_WeeklyFarming=False, RogueWorld_UseImmersifier=False,
                            RogueWorld_UseStamina=False, RogueWorld_DoubleEvent=False,
                            RogueDebug_DebugMode=False)
        elif mode == 'CurrencyWars':
            from tasks.currency_wars.currency_wars import CurrencyWars
            runner = CurrencyWars(self.config, device=self.device)
        elif mode == 'DivergentUniverse':
            from tasks.divergent_universe.divergent_universe import DivergentUniverse
            runner = DivergentUniverse(self.config, device=self.device)
            settings['DivergentUniverse_StopAtWeeklyLimit'] = False
            # First-station farming is for fitting levels, not weekly completion.
            settings['DivergentUniverse_Mode'] = 'full'
        else:
            raise RequestHumanTakeover(f'未知的周积分模式：{mode}')
        overridden = self.config.overridden.copy()
        self.config.override(**settings)
        try:
            if mode == 'Rogue':
                runner.rogue_once()
            else:
                runner.run(scheduled=False)
        finally:
            self.config.overridden = overridden
            self.config.bind(self.config.task)

    def schedule_points(self, points):
        current, total = points
        if current >= total:
            logger.info(f'Shared weekly points complete: {current}/{total}')
            self.config.task_delay(target=get_server_next_monday_update(self.config.Scheduler_ServerUpdate))
            return True
        self.config.task_delay(minute=0)
        return False

    def run(self):
        mode = self.config.RogueWeekly_Mode
        if not self.config.is_task_supported(mode):
            raise RequestHumanTakeover('当前设备后端不支持所选的周积分模式')
        before = self.read_points()
        if before[0] >= before[1]:
            self.schedule_points(before)
            return
        # Higher-priority work may have become due while reading the guide.
        if self.config.task_switched():
            return
        self.play_once(mode)
        after = self.read_points()
        if after[0] <= before[0] and after[1] == before[1]:
            # A loss/early exit may grant no points. Avoid an unlimited retry loop.
            logger.warning('本局没有增加共享周积分，一小时后重试')
            self.config.task_delay(minute=60)
            return
        self.schedule_points(after)
