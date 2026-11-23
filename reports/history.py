from reports.repository import ReportRepository, RestartReport


def read_report(repository: ReportRepository, report_id: int):
    report = repository.get_report(report_id)
    if report is None:
        raise KeyError(report_id)
    return report


def read_restart_report(repository: ReportRepository, report_id: int) -> RestartReport:
    report = repository.get_restart_report(report_id)
    if report is None:
        raise KeyError(report_id)
    return report
