"""Print a revenue report against the infrastructure cost floor."""
from django.core.management.base import BaseCommand

from billing import accounting


class Command(BaseCommand):
    help = 'Show trailing revenue and this month vs the infra cost floor.'

    def add_arguments(self, parser):
        parser.add_argument('--months', type=int, default=6)

    def handle(self, *args, **options):
        for r in accounting.revenue_summary(months=options['months']):
            cur = ', '.join(f"{v:.0f} {k}" for k, v in r['by_currency'].items()) or '0'
            self.stdout.write(f"{r['year']}-{r['month']:02d}: {cur} "
                              f"({r['paying_users']} paying, {r['payment_count']} payments)")
        net = accounting.cost_floor_net()
        verdict = 'ABOVE' if net['above_floor'] else 'BELOW'
        self.stdout.write(self.style.SUCCESS(
            f"This month: revenue {net['revenue']:.0f} vs floor {net['floor']:.0f} "
            f"-> {verdict} by {abs(net['net']):.0f}"))
