
@register_handler
def check_scan_zone(series_uid: DicomUID,
                    pacs_local: PACS = CDepends(make_local_pacs),
                    pacs_petrova: PACS = CDepends(get_pacs_petrova)):
    series = pacs_local.get_series(series_uid = series_uid,
                                   pacs = pacs_petrova)
    if 'CR' in series.tags.modalities:
        return SUCCES
    else:
        return FAIL
    



