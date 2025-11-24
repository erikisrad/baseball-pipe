from datetime import datetime, timedelta

def get_date(days_ago=None, date_str=None):
    
    if date_str:
        date = datetime.strptime(date_str, "%Y%m%d")
    else:
        date = datetime.now()

    if days_ago:
        date = (date - timedelta(days=days_ago))

    return date

def pretty_print_date(date):
    pretty_time = date.strftime("%A, %B %d %Y")
    return pretty_time