import cmk
import cmk.gui.cron as cron


def test_registered_jobs():

    expected = [
        'cmk.gui.inventory.run',
        'cmk.gui.plugins.cron.gui_background_job.housekeeping',
        'cmk.gui.userdb.execute_userdb_job',
        'cmk.gui.wato.execute_network_scan_job',
    ]

    if not cmk.is_raw_edition():
        expected += [
            'cmk.gui.cee.reporting.cleanup_stored_reports',
            'cmk.gui.cee.reporting.do_scheduled_reports',
        ]

    found_jobs = sorted(["%s.%s" % (f.__module__, f.__name__) for f in cron.multisite_cronjobs])
    assert found_jobs == sorted(expected)
