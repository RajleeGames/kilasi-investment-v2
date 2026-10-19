from django.urls import path

from . import views
from kilasi_transport import transport_auth
from . import novman_views
from . import novman_tools
from . import kilasi_retail
from . import kilasi_finance


urlpatterns = [
    path('retail/', kilasi_retail.overview, name='retail_overview'),
    path('retail/finance/', kilasi_finance.overview, name='kilasi_finance_overview'),
    path('sales/<int:pk>/document/', kilasi_finance.sales_document, name='kilasi_sales_document'),
    path('branch/switch/', novman_views.branch_switch, name='branch_switch'),
    path('branches/', novman_tools.branch_manage, name='branch_manage'),
    path('stock-transfers/', novman_views.stock_transfers, name='stock_transfers'),
    path(
        'login/',
        transport_auth.login_view,
        name='login',
    ),
    path(
        'logout/',
        views.logout_view,
        name='logout',
    ),
    path(
        '',
        novman_views.dashboard,
        name='dashboard',
    ),

    # ------------------------------------------------------------
    # POS
    # ------------------------------------------------------------
    path(
        'pos/',
        novman_views.pos,
        name='pos',
    ),
    path(
        'pos/checkout/',
        novman_views.pos_checkout,
        name='pos_checkout',
    ),

    # ------------------------------------------------------------
    # Products
    # ------------------------------------------------------------
    path(
        'products/',
        novman_views.products,
        name='products',
    ),
    path(
        'products/add/',
        views.product_create,
        name='product_create',
    ),
    path(
        'products/<int:pk>/edit/',
        views.product_edit,
        name='product_edit',
    ),
    path(
        'products/<int:pk>/toggle/',
        views.product_toggle,
        name='product_toggle',
    ),
    path(
        'products/<int:pk>/delete/',
        views.product_delete,
        name='product_delete',
    ),

    path(
        'units/<int:pk>/update/',
        views.unit_update,
        name='unit_update',
    ),
    path(
        'units/<int:pk>/delete/',
        views.unit_delete,
        name='unit_delete',
    ),

    path(
        'categories/add/',
        views.category_create,
        name='category_create',
    ),
    path(
        'categories/<int:pk>/update/',
        views.category_update,
        name='category_update',
    ),
    path(
        'categories/<int:pk>/delete/',
        views.category_delete,
        name='category_delete',
    ),

    # ------------------------------------------------------------
    # Stock
    # ------------------------------------------------------------
    path(
        'stock/',
        novman_views.stock,
        name='stock',
    ),

    path(
        'stock/product/<int:pk>/',
        novman_views.stock_detail,
        name='stock_detail',
    ),

    path(
        'stock/batch/<int:pk>/cost/',
        novman_views.stock_batch_cost_edit,
        name='stock_batch_cost_edit',
    ),

    path(
        'stock/opening/',
        novman_views.opening_stock,
        name='opening_stock',
    ),

    path(
        'stock/opening/<int:pk>/delete/',
        novman_views.opening_batch_delete,
        name='opening_batch_delete',
    ),

    path(
        'stock/adjust/',
        novman_views.stock_adjust,
        name='stock_adjust',
    ),

    # ------------------------------------------------------------
    # Purchases
    # ------------------------------------------------------------
    path(
        'purchases/',
        novman_views.purchases,
        name='purchases',
    ),

    path(
        'purchases/add/',
        novman_views.purchase_create,
        name='purchase_create',
    ),

    path(
        'purchases/<int:pk>/edit/',
        novman_views.purchase_edit,
        name='purchase_edit',
    ),

    path(
        'purchases/<int:pk>/delete/',
        novman_views.purchase_delete,
        name='purchase_delete',
    ),

    # ------------------------------------------------------------
    # Sales
    # ------------------------------------------------------------
    path(
        'sales/',
        novman_views.sales,
        name='sales',
    ),

    path(
        'sales/<int:pk>/',
        novman_views.sale_detail,
        name='sale_detail',
    ),

    path(
        'sales/<int:pk>/void/',
        novman_views.sale_void,
        name='sale_void',
    ),

    # ------------------------------------------------------------
    # Debts
    # ------------------------------------------------------------
    path(
        'debts/',
        views.debts,
        name='debts',
    ),

    path(
        'debts/customers/<int:pk>/',
        views.customer_detail,
        name='customer_detail',
    ),

    path(
        'debts/customers/<int:pk>/edit/',
        views.customer_edit,
        name='customer_edit',
    ),

    path(
        'debts/customers/<int:pk>/toggle/',
        views.customer_toggle,
        name='customer_toggle',
    ),

    path(
        'debts/customers/<int:pk>/payments/add/',
        novman_views.customer_payment_create,
        name='customer_payment_create',
    ),

    path(
        'debts/payments/<int:pk>/void/',
        novman_views.customer_payment_void,
        name='customer_payment_void',
    ),

    # ------------------------------------------------------------
    # Ninaowadai
    # ------------------------------------------------------------
    path(
        'ninaowadai/',
        views.ninaowadai,
        name='ninaowadai',
    ),

    path(
        'ninaowadai/<int:pk>/payments/add/',
        views.ninaowadai_payment_create,
        name='ninaowadai_payment_create',
    ),

    path(
        'ninaowadai/<int:pk>/delete/',
        views.ninaowadai_delete,
        name='ninaowadai_delete',
    ),

    # ------------------------------------------------------------
    # End day
    # ------------------------------------------------------------
    path(
        'end-day/',
        novman_views.end_day,
        name='end_day',
    ),

    path(
        'end-day/<int:pk>/amend/',
        novman_views.blocked_export,
        name='cash_closing_amend',
    ),

    # ------------------------------------------------------------
    # Stocktake
    # ------------------------------------------------------------
    path(
        'stocktakes/',
        novman_views.stocktakes,
        name='stocktakes',
    ),

    path(
        'stocktakes/<int:pk>/',
        novman_views.stocktake_detail,
        name='stocktake_detail',
    ),

    # ------------------------------------------------------------
    # Data tools
    # ------------------------------------------------------------
    path(
        'data-tools/',
        novman_tools.data_tools,
        name='data_tools',
    ),

    path(
        'data-tools/export/<str:kind>/',
        novman_tools.export_csv,
        name='export_csv',
    ),

    path(
        'data-tools/export-all/',
        novman_tools.export_all_zip,
        name='export_all_zip',
    ),

    path(
        'data-tools/backup/',
        novman_tools.backup_download,
        name='backup_download',
    ),

    # ------------------------------------------------------------
    # Expenses
    # ------------------------------------------------------------
    path(
        'expenses/',
        novman_views.expenses,
        name='expenses',
    ),

    path(
        'expenses/<int:pk>/edit/',
        novman_views.expense_edit,
        name='expense_edit',
    ),

    path(
        'expenses/<int:pk>/delete/',
        novman_views.expense_delete,
        name='expense_delete',
    ),

    # ------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------
    path(
        'reports/',
        novman_views.reports,
        name='reports',
    ),

    # ------------------------------------------------------------
    # Users
    # ------------------------------------------------------------
    path(
        'users/',
        views.users,
        name='users',
    ),

    path(
        'users/add/',
        views.user_create,
        name='user_create',
    ),

    path(
        'users/<int:pk>/edit/',
        views.user_edit,
        name='user_edit',
    ),

    path(
        'users/<int:pk>/toggle/',
        views.user_toggle,
        name='user_toggle',
    ),

    path(
        'users/<int:pk>/delete/',
        views.user_delete,
        name='user_delete',
    ),

    # ------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------
    path(
        'settings/',
        views.settings_view,
        name='settings',
    ),

    # ------------------------------------------------------------
    # QZ Tray
    # ------------------------------------------------------------
    path(
        'qz/cert/',
        views.qz_certificate,
        name='qz_certificate',
    ),

    path(
        'qz/sign/',
        views.qz_sign,
        name='qz_sign',
    ),
]