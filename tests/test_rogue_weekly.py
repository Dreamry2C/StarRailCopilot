"""Scheduling regressions without touching profiles or driving a device."""
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.config.config import AzurLaneConfig, TaskEnd
from module.config.config_updater import ConfigUpdater
from module.config.deep import deep_get, deep_set
from module.config.utils import get_server_next_monday_update
from module.exception import RequestHumanTakeover
from tasks.currency_wars.currency_wars import CurrencyWars
from tasks.divergent_universe.divergent_universe import DivergentUniverse
from tasks.rogue_weekly.scheduler import finish_batch
from tasks.rogue_weekly.weekly import RogueWeekly


class MemoryConfig(AzurLaneConfig):
    def __init__(self, task='RogueWeekly'):
        self.disk = json.loads(Path('config/template.json').read_text(encoding='utf-8'))
        for values in self.disk.values():
            if 'Scheduler' in values:
                values['Scheduler']['Enable'] = False
        deep_set(self.disk, 'Rogue.RogueWorld.UseImmersifier', False)
        deep_set(self.disk, 'Rogue.RogueWorld.DoubleEvent', False)
        deep_set(self.disk, 'Alas.Emulator.GameLanguage', 'cn')
        deep_set(self.disk, f'{task}.Scheduler.Enable', True)
        super().__init__('unit-roguelike', task=task)

    def read_file(self, *args, **kwargs):
        return self.config_update(deepcopy(self.disk))

    def write_file(self, config_name, data, **kwargs):
        self.disk = deepcopy(data)


class WeeklySchedulingTests(unittest.TestCase):
    def make_task(self):
        config = MemoryConfig()
        ui = RogueWeekly(config, device=Mock())
        ui.play_once = Mock()
        return config, ui

    def test_full_points_do_not_start_a_run_and_next_monday_survives_reload(self):
        config, ui = self.make_task()
        ui.read_points = Mock(return_value=(18000, 18000))
        ui.run()
        ui.play_once.assert_not_called()
        monday = get_server_next_monday_update(config.Scheduler_ServerUpdate)
        config.load()
        self.assertEqual(config.cross_get('RogueWeekly.Scheduler.NextRun'), monday)
        self.assertTrue(config.cross_get('RogueWeekly.Scheduler.Enable'))

    def test_weekly_delay_exceeding_one_day_is_not_reset(self):
        config = MemoryConfig()
        future = datetime.now().replace(microsecond=0) + timedelta(days=6)
        config.task_delay(target=future)
        config.load()
        self.assertEqual(config.cross_get('RogueWeekly.Scheduler.NextRun'), future)

    def test_one_run_reaches_limit(self):
        config, ui = self.make_task()
        ui.read_points = Mock(side_effect=[(15000, 18000), (18000, 18000)])
        ui.run()
        ui.play_once.assert_called_once_with('CurrencyWars')
        self.assertGreater(config.cross_get('RogueWeekly.Scheduler.NextRun'), datetime.now())

    def test_partial_week_returns_to_scheduler(self):
        config, ui = self.make_task()
        ui.read_points = Mock(side_effect=[(6000, 18000), (9000, 18000)])
        ui.run()
        ui.play_once.assert_called_once()
        self.assertLessEqual(config.cross_get('RogueWeekly.Scheduler.NextRun'), datetime.now())

    def test_no_progress_does_not_spin_forever(self):
        config, ui = self.make_task()
        ui.read_points = Mock(return_value=(6000, 18000))
        ui.run()
        ui.play_once.assert_called_once()
        self.assertGreater(config.cross_get('RogueWeekly.Scheduler.NextRun'), datetime.now() + timedelta(minutes=59))

    def test_due_daily_task_prevents_starting_another_run(self):
        config, ui = self.make_task()
        ui.read_points = Mock(return_value=(6000, 18000))
        config.task_call('Dungeon')
        ui.run()
        ui.play_once.assert_not_called()

    def test_invalid_ocr_cannot_complete_week_or_overwrite_record(self):
        config, ui = self.make_task()
        ui.dungeon_tab_goto = Mock()
        ui.dungeon_nav_goto = Mock()
        ui.loop = Mock(return_value=iter([None]))
        config.stored.SimulatedUniverse.set(5000, 18000)
        with patch('tasks.rogue_weekly.weekly.OcrSimUniPoint') as ocr, patch('tasks.rogue_weekly.weekly.Timer') as timer:
            ocr.return_value.ocr_single_line.return_value = (18000, 0, 0)
            timer.return_value.start.return_value.reached.return_value = True
            with self.assertRaises(RequestHumanTakeover):
                ui.read_points()
        self.assertEqual(config.stored.SimulatedUniverse.value, 5000)

    def test_selected_mode_keeps_settings_and_weekly_scheduler_identity(self):
        for mode, module in [('CurrencyWars', 'tasks.currency_wars.currency_wars.CurrencyWars'),
                             ('DivergentUniverse', 'tasks.divergent_universe.divergent_universe.DivergentUniverse'),
                             ('Rogue', 'tasks.rogue.rogue.Rogue')]:
            with self.subTest(mode=mode):
                config = MemoryConfig()
                # Different from generated defaults, to catch accidental default binding.
                if mode == 'CurrencyWars':
                    config.cross_set('CurrencyWars.CurrencyWars.Difficulty', 'highest')
                elif mode == 'DivergentUniverse':
                    config.cross_set('DivergentUniverse.DivergentUniverse.TeamPreset', 7)
                before = deepcopy(config.data[mode])
                observed = []

                def run(**kwargs):
                    config.update()  # Progress writes must not lose selected settings.
                    observed.append(config.task.command)
                    if mode == 'CurrencyWars':
                        self.assertEqual(config.CurrencyWars_Difficulty, 'highest')
                    elif mode == 'DivergentUniverse':
                        self.assertEqual(config.DivergentUniverse_TeamPreset, 7)
                        self.assertEqual(config.DivergentUniverse_Mode, 'full')
                    else:
                        self.assertFalse(config.RogueWorld_UseStamina)
                    if mode != 'Rogue':
                        self.assertEqual(kwargs, {'scheduled': False})
                    raise TaskEnd

                with patch(module) as runner:
                    runner.return_value.run.side_effect = run
                    runner.return_value.rogue_once.side_effect = run
                    with self.assertRaises(TaskEnd):
                        RogueWeekly(config, device=Mock()).play_once(mode)
                self.assertEqual(observed, ['RogueWeekly'])
                self.assertEqual(config.data[mode], before)
                self.assertEqual(config.overridden, {})


class BatchSchedulingTests(unittest.TestCase):
    def test_continuous_modes_take_turns(self):
        config = MemoryConfig('CurrencyWars')
        config.cross_set('DivergentUniverse.Scheduler.Enable', True)
        finish_batch(config, 0, False)
        self.assertEqual(config.get_next().command, 'DivergentUniverse')

    def test_continuous_currency_yields_to_due_daily_task(self):
        config = MemoryConfig('CurrencyWars')
        self.assertFalse(finish_batch(config, 0, False))
        config.task_call('Dungeon')
        self.assertEqual(config.get_next().command, 'Dungeon')
        self.assertTrue(config.cross_get('CurrencyWars.Scheduler.Enable'))

    def test_finite_batch_keeps_count_across_reloads_then_disables(self):
        for mode in ('CurrencyWars', 'DivergentUniverse'):
            with self.subTest(mode=mode):
                config = MemoryConfig(mode)
                self.assertFalse(finish_batch(config, 2, False))
                config.update()
                self.assertEqual(config.RogueBatch_CompletedRuns, 1)
                self.assertTrue(finish_batch(config, 2, False))
                self.assertFalse(config.Scheduler_Enable)
                self.assertEqual(config.RogueBatch_CompletedRuns, 0)

    def test_daily_repeat_keeps_task_enabled(self):
        config = MemoryConfig('CurrencyWars')
        self.assertTrue(finish_batch(config, 1, True))
        self.assertTrue(config.Scheduler_Enable)
        self.assertGreater(config.Scheduler_NextRun, datetime.now())

    def test_currency_continuous_setting_runs_only_one_recovery_unit(self):
        config = MemoryConfig('CurrencyWars')
        config.override(CurrencyWars_Runs=0)
        campaign = SimpleNamespace(profile='unit', data={'settlements': []}, reload=Mock())
        task = CurrencyWars(config, device=Mock())
        task._run = Mock()
        task._run_with_recovery(campaign)
        task._run.assert_called_once_with(1)

    def test_currency_recovered_settlement_does_not_start_second_run(self):
        config = MemoryConfig('CurrencyWars')
        campaign = SimpleNamespace(profile='unit', data={'settlements': []}, reload=Mock(), heartbeat=Mock())
        task = CurrencyWars(config, device=Mock())
        task._restart_currency_game = Mock()

        def settle_then_fail(count):
            campaign.data['settlements'].append({'profile': 'unit'})
            raise RuntimeError('failure after durable settlement')

        task._run = Mock(side_effect=settle_then_fail)
        with patch('tasks.currency_wars.currency_wars.save_error_log'):
            task._run_with_recovery(campaign)
        task._run.assert_called_once_with(1)
        task._restart_currency_game.assert_not_called()

    def test_divergent_continuous_setting_plays_once(self):
        config = MemoryConfig('DivergentUniverse')
        config.override(DivergentUniverse_Runs=0, DivergentUniverse_ClaimRewards=False)
        task = DivergentUniverse(config, device=Mock())
        task.error_frame_history = nullcontext
        task._initialize = Mock()
        task.go_to_lobby = Mock(return_value=True)
        task.resume_failed_position = Mock()
        task.play_run = Mock(return_value='win')
        with patch('tasks.divergent_universe.divergent_universe.Progress') as progress, \
                patch('tasks.divergent_universe.divergent_universe.FileLock', return_value=nullcontext()), \
                patch('tasks.rogue_weekly.scheduler.leave_lobby'):
            task.run()
        task.play_run.assert_called_once()
        progress.return_value.finish.assert_called_once_with('win')
        self.assertTrue(config.Scheduler_Enable)
        self.assertLessEqual(config.Scheduler_NextRun, datetime.now())

    def test_divergent_point_limit_does_not_count_a_settlement(self):
        config = MemoryConfig('DivergentUniverse')
        config.override(DivergentUniverse_ClaimRewards=False)
        config.RogueBatch_CompletedRuns = 2
        task = DivergentUniverse(config, device=Mock())
        task.error_frame_history = nullcontext
        task._initialize = Mock()
        task.go_to_lobby = Mock(return_value=False)
        task.play_run = Mock()
        with patch('tasks.divergent_universe.divergent_universe.Progress') as progress, \
                patch('tasks.divergent_universe.divergent_universe.FileLock', return_value=nullcontext()), \
                patch('tasks.rogue_weekly.scheduler.leave_lobby'):
            self.assertEqual(task.run(), 0)
        task.play_run.assert_not_called()
        progress.return_value.finish.assert_not_called()
        self.assertEqual(config.RogueBatch_CompletedRuns, 0)
        self.assertEqual(config.Scheduler_NextRun, get_server_next_monday_update(config.Scheduler_ServerUpdate))


class ConfigurationTests(unittest.TestCase):
    def test_migrate_shared_record_and_preserve_mode_settings(self):
        old = json.loads(Path('config/template.json').read_text(encoding='utf-8'))
        old.pop('RogueWeekly')
        record = {'time': '2026-11-07 04:00:00', 'value': 9000, 'total': 18000}
        deep_set(old, 'Rogue.RogueStorage.SimulatedUniverse', record)
        deep_set(old, 'Dungeon.DungeonStorage.SimulatedUniverse', dict(record, value=1000))
        deep_set(old, 'CurrencyWars.CurrencyWars.Runs', 0)
        deep_set(old, 'CurrencyWars.Scheduler.Enable', True)
        deep_set(old, 'DivergentUniverse.DivergentUniverse.TeamPreset', 7)
        old['DivergentUniverse'].pop('Scheduler')
        new = ConfigUpdater().config_update(old)
        self.assertEqual(deep_get(new, 'RogueWeekly.RogueStorage.SimulatedUniverse'), record)
        self.assertTrue(deep_get(new, 'CurrencyWars.Scheduler.Enable'))
        self.assertFalse(deep_get(new, 'DivergentUniverse.Scheduler.Enable'))
        self.assertEqual(deep_get(new, 'DivergentUniverse.DivergentUniverse.TeamPreset'), 7)

    def test_new_record_wins_over_legacy_record(self):
        old = json.loads(Path('config/template.json').read_text(encoding='utf-8'))
        deep_set(old, 'RogueWeekly.RogueStorage.SimulatedUniverse.value', 12000)
        deep_set(old, 'Rogue.RogueStorage.SimulatedUniverse.value', 1000)
        self.assertEqual(deep_get(ConfigUpdater().config_update(old), 'RogueWeekly.RogueStorage.SimulatedUniverse.value'), 12000)

    def test_all_modes_are_registered_as_scheduled_tasks(self):
        from src import StarRailCopilot
        menu = json.loads(Path('module/config/argument/menu.json').read_text(encoding='utf-8'))
        self.assertIn('RogueWeekly', menu['Weekly']['tasks'])
        self.assertEqual(set(menu['Roguelike']['tasks']), {'Rogue', 'CurrencyWars', 'DivergentUniverse'})
        self.assertEqual(menu['Roguelike']['page'], 'setting')
        for task in ('RogueWeekly', 'Rogue', 'CurrencyWars', 'DivergentUniverse'):
            self.assertIn(task, MemoryConfig(task).SCHEDULER_PRIORITY)
        self.assertTrue(callable(StarRailCopilot.rogue_weekly))
        self.assertNotIn('DivergentUniverse', menu['Tool']['tasks'])


if __name__ == '__main__':
    unittest.main()
