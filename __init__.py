from . import controllers
from . import models

import odoo.addons.payment as payment


def post_init_hook(env):
    payment.setup_provider(env, 'raiffeisen')


def uninstall_hook(env):
    payment.reset_payment_provider(env, 'raiffeisen')
