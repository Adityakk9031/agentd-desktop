---
name: online-shopping
description: Find, compare and put products in a basket on shopping websites, then stop for the user to pay.
---

1. Understand what to buy: item, quantity, size or variant, budget, and any brand or delivery wishes. Ask with wait_for_user only if something essential is missing.
2. Search with web_search, or open the shop and use its own search box (browser_type with press_enter).
3. Compare at least three options. Note for each: name, price including delivery, rating, delivery date, seller.
4. Pick the best fit, open it, choose size or variant with browser_select, and add the right quantity to the basket.
5. Go to the basket and check items, quantities and the total.
6. Sign in with browser_fill_login if a login is saved for the site; otherwise call wait_for_user to let the user sign in.
7. Fill in the delivery details the user has given you. Never type card numbers, security codes or one-time codes.
8. Stop at the final pay or place-order step. Tell the user what is in the basket, the total, and the delivery date. The last click needs their approval and they may need to enter payment details themselves.

If the site shows a check that you are human, or anything asks for a code, call wait_for_user.
