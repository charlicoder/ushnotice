# Run these SQL queries in your ushanr database to get the values:

SELECT id FROM companies LIMIT 1;
-- → paste into USHANR_COMPANY_ID

SELECT id FROM journals WHERE name ILIKE '%receivable%' LIMIT 1;
-- → paste into USHANR_AR_JOURNAL_ID

SELECT id FROM accounts WHERE code ILIKE '4%' LIMIT 5;
-- → find your revenue account → paste into USHANR_REVENUE_ACCOUNT_ID


