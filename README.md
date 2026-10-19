# Mega Fish Point

A clean Django POS + stock system for fish, chicken, milk, eggs, dagaa, korosho and similar products.

## Main features
- Admin + Cashier roles
- POS with quantity decimals and money discounts
- Products with multiple selling units (egg / tray, 250ml / 500ml / litre)
- Purchase batches with FIFO costing
- Opening stock without fake purchases (can be entered by KG, tray, litre-size unit, etc.)
- Stock adjustments (spoilage, loss, correction)
- Purchases with variable supplier cost
- Sales history + printable receipt
- Expenses
- Dashboard and profit reports
- One global `app.css` and one global `app.js`
- Money with commas and clean quantities (1, 1.5, 2.25)

## Windows quick start
1. Install Python 3.11+.
2. Open CMD/PowerShell inside this folder.
3. Run:

```powershell
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python manage.py makemigrations core
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

Open: http://127.0.0.1:8000

### Demo logins
- Admin: `admin` / `Mega@12345`
- Cashier: `cashier` / `Cashier@12345`

Change these passwords before real use.

## Important stock logic
- Every purchase creates a stock batch at that real cost.
- FIFO consumes the oldest remaining batch first.
- Opening stock creates an `OPENING` batch and does not count as a purchase.
- A tray of 30 eggs still deducts 30 egg pieces from one stock pool.
- Milk can use ML as the base stock and sell 250ML, 500ML, 1L, etc.
- Selling prices can be edited later without changing past sales.
- Cashier uses money discount; Admin can see the discount amount in sales history.
