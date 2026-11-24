from . import calendar, dashboard, employees, orders, reports, services, settings, users, warehouse

BLUEPRINTS = (dashboard.bp, orders.bp, calendar.bp, services.bp, employees.bp, warehouse.bp, users.bp,
              reports.bp, settings.bp)
