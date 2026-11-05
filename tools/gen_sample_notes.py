#!/usr/bin/env python3
# tools/gen_sample_notes.py - generate 30 realistic sample notes about a small
# business into ./notes so Jarvis works on first run with zero setup.
# (The build pack's own instruction for people with no notes yet.)

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOTES = os.path.join(ROOT, "notes")

B = "Cardinal & Co. Coffee Roasters"
N = {
# ---------------- ops ----------------
"ops/opening-hours.md": f"""# Opening hours

{B} is open Monday to Friday from 7:00 to 18:00, Saturday 8:00 to 17:00, and Sunday 9:00 to 15:00. The espresso machine is warm by 6:40; first pour is on the bar at 6:55 so the early queue never waits.

Quiet hours are 14:00 to 16:00 on weekdays - good slot for cuppings and staff breaks. The shop closes to walk-ins at closing time minus 15 minutes; the last coffee goes out at the bell.

Key holders open and close per the [[Staff rota]]. If a shift leader is sick, opening falls to whoever holds a key and lives nearest; the list is pinned in the back office next to the [[Emergency procedures]].""",
"ops/roasting-profiles.md": f"""# Roasting profiles

Our Diedrich IR-12 runs three house profiles. The dense Brazilian (Harbor Green's Cerrado lot) roasts 11:30 total with first crack at 9:40, dropped medium. The washed Ethiopian goes lighter: 10:45 total, first crack 9:10, rolled straight into the cooling tray to keep the florals.

Every batch is logged by hand: green temp, charge temp, turning point, FC time, drop time. Logs live in the binder on the roastery shelf; Ana photographs them into the shared drive weekly.

Rest days matter more than most people think - see [[Bean storage]]. Wholesale bags roast on Wednesdays so the [[Wholesale clients]] get 4-day-rested coffee every Monday delivery.""",
"ops/equipment-maintenance.md": f"""# Equipment maintenance

The Linea Mini gets backflushed with Cafiza every night the shop is open, and a full descale is booked for the last Sunday of each quarter. Group gaskets changed in August 2026 - next change due around February.

The EK43 burrs were replaced in June 2026; alignment checked monthly with the feeler gauge in the drawer under the till. If grind quality drifts, check [[Grinder settings]] before touching anything else.

The roaster's chaff drawer is emptied mid-roast-day without exception; the drum gets a full brush-down every Saturday after service. Any fault goes in the maintenance log the same day - the log saved us during the [[Health inspection]].""",
"ops/grinder-settings.md": f"""# Grinder settings

House dial-in on the EK43: 8.5 for espresso on the Brazilian, 9.0 for the Ethiopian light roast, 4.5 for batch brew. These drift with roast age - coffee older than 14 days pulls tighter, so drop the setting by half a notch.

Rule of the bar: taste before you trust the numbers. If shots run fast and sour, grind finer in small steps and write the change on the whiteboard above the grinder with your initials and the time. Beans rest differently after delivery day - see [[Bean storage]] for why the dial moves on Tuesdays and Fridays.""",
"ops/shop-layout.md": f"""# Shop layout

Twelve covers inside, six on the pavement terrace. The till and the pastry case sit left of the entrance so the queue flows right-handed toward the bar. Retail shelf is at the back wall - bags face out, roast date visible, [[Bean storage]] rules keep the shelf stock rotated.

The roastery occupies the back third behind the glass partition; customers like watching, so keep the curtain open during roast days. The WiFi router lives under the till; the password changes monthly and goes on the receipt footer. Fire exits are marked on the plan taped inside the [[Emergency procedures]] folder.""",
"ops/health-inspection.md": f"""# Health inspection

Last full inspection was 12 September 2026: five stars, no follow-up required. The officer's only note was to re-date the open milk labels - done the same afternoon.

Keep it that way: temps logged twice daily (fridge 4C, milk fridge 3C, dish machine 82C at the sanitise rinse), probe wipes between uses, and the pest contract visit falls on the first Monday of every month. All records live in the blue binder in the back office; the same binder holds the [[Equipment maintenance]] log and the [[Emergency procedures]].""",
"ops/emergency-procedures.md": f"""# Emergency procedures

Gas valve for the roaster burner is behind the red panel on the roastery wall - turn right to cut. Fire exits: the main door, and the back corridor door past the offices; both stay clear at all times, no deliveries parked in front.

First aid kit is under the till; the accident book is in the same drawer. Burn guidance posters hang on either side of the roaster. Emergency contacts are taped inside the blue binder: building manager, gas engineer, and Priya at the accountant for anything insurance-related - see [[Insurance]] and [[Rent agreement]].""",
# ---------------- suppliers ----------------
"suppliers/suppliers-beans.md": f"""# Suppliers beans

Our green coffee comes from Harbor Green Trading, the import house we signed with in spring 2025 - full terms in the [[Harbor Green deal]] note. Deliveries arrive on Tuesday and Friday mornings between 8:00 and 9:30, always the Cerrado Brazil and a rotating washed lot; the Friday delivery also tops up whatever the weekend will burn through.

Backup stock sits at Roastworks, a fortnight's supply at our peak, invoiced on request. Never let green stock fall below two weeks of roast capacity - that number lives in the [[Cashflow Q4]] sheet. Milk and pastries are separate supply chains: see the [[Milk supplier]] and [[Pastry supplier]] notes.""",
"suppliers/harbor-green-deal.md": f"""# Harbor Green deal

Contract with Harbor Green Trading runs to March 2027: fixed price on the Cerrado Brazil at $4.10/kg green, floating on washed lots at C-price plus differential. Payment terms are 30 days net; the invoice lands on delivery day, Priya pays from the [[Accounting basics]] cycle.

Two 60kg bags minimum per drop, no drop fee within the M25. If a lot we love comes through their offer list, we can request a reservation up to five bags with 60 days' notice - that is how we held the Guji lot last spring. Deliveries and the ordering rhythm are described in [[Suppliers beans]].""",
"suppliers/milk-supplier.md": f"""# Milk supplier

Dairy Fresh delivers whole and semi-skimmed Monday and Thursday at 6:15, before the door opens. Crate goes straight into the milk fridge - 3C, checked at open and mid-afternoon per the [[Health inspection]] sheet. Oat and the other alt milks come through the wholesale driver on the Tuesday delivery from Harbor Green's sister catalogue, not Dairy Fresh.

Order rhythm: two crates Monday, two Thursday, one extra on any [[Events calendar]] weekend. Never stack a third crate "just in case" - spoilage eats the margin, as the [[Cashflow Q4]] review showed in August.""",
"suppliers/pastry-supplier.md": f"""# Pastry supplier

Flour Lane Bakery van arrives daily at 6:30 - croissants, pain au raisin, and the cardamom buns that sell out by eleven. Order sheet locks at 16:00 the day before; the morning baker texts the count to the Flour Lane line.

Cardamom bun count follows a simple rule: 24 on weekdays, 40 on Saturdays, 18 on Sundays. Anything unsold after 15:00 goes half price at the till. During [[Events calendar]] weekends add a tray of the almond danish - it photographed beautifully at the September cupping and sold out before noon.""",
# ---------------- menu ----------------
"menu/espresso-menu.md": f"""# Espresso menu

The bar list: espresso 2.80, flat white 3.60, cappuccino 3.40, latte 3.80 (8 or 12 oz), batch brew 2.50, pour-over 4.50. Iced drinks carry a 30p uplift in summer. House blend for espresso is the Brazilian alone when it is under 21 days off roast; older than that it blends 80/20 with the washed Ethiopian to keep the body up - see [[Roasting profiles]] and [[Grinder settings]].

Every drink gets a complimentary sparkling water palate cleanser on the saucer. It costs almost nothing and it is the single most-mentioned line in the [[Feedback log]].""",
"menu/seasonal-menu.md": f"""# Seasonal menu

Autumn 2026 lineup runs to end of November: the cardamom cortado (cardamom syrup house-made, 4.20), the fig and walnut loaf from [[Pastry supplier]] Flour Lane, and the spiced batch brew on Saturdays only. The cortado took off after the September post - the [[Instagram strategy]] reel did 40k views.

Winter planning starts mid-November: mulled batch brew is confirmed, the gingerbread syrup recipe needs a cost pass with Priya before we print menus. Seasonal drinks never stay past their season; the menu board gets wiped the first Monday of December per the [[Marketing plan]].""",
"menu/brew-recipes.md": f"""# Brew recipes

Pour-over standard: 20g coffee to 320g water at 94C, three pours - bloom 50g for 40 seconds, then two 135g pours, total drawdown about 3 minutes. Batch brew on the Moccamaster: 60g per litre, paper filters, holding time capped at 40 minutes before the urn is dumped and refilled.

Espresso house dial-in starts at 18g in, 40g out, 28 to 30 seconds; adjust per [[Grinder settings]] and roast age. All recipes are printed on laminated cards at the bar - if a card is missing or wrong, the version of record lives in this note.""",
# ---------------- people ----------------
"people/staff-rota.md": f"""# Staff rota

The rota publishes two weeks ahead, pinned in the back office and sent as a photo to the group chat. Ana opens Tuesday to Friday; Marcus covers Saturday and Sunday opens; I float between bar and roastery - roast days are Tuesday and Friday mornings, see [[Roasting profiles]].

Shift pattern: opens 6:30, closes 18:15 with the last half hour for closing clean per the [[Equipment maintenance]] checklist. Swaps are allowed between staff with 24 hours' notice to me, never a no-show swap. Payday is the 28th, run by Priya - see [[Accounting basics]].""",
"people/ana-the-barista.md": f"""# Ana the barista

Ana has been with us since March 2024 and runs the bar like a metronome. She took bronze at the regional latte art throwdown in June 2026 - the tulip on our flat whites is hers, and the [[Staff training]] plan for new hires uses her pour as the reference video.

Tuesdays are her days off. She wants to learn roasting this winter: first step is sitting through a full [[Roasting profiles]] session and the [[Cupping notes]] ritual every Thursday. She asked for the words "patience first" on any training plan - she is right, and it matches how she trains the new starters.""",
"people/staff-training.md": f"""# Staff training

Every new hire runs the same two-week plan. Week one: shadow service, learn the till, memorise the [[Espresso menu]] prices, and read the [[Health inspection]] rules before touching milk. Week two: dial-in practice with Ana's reference pours, batch brew, and a solo shift with me floating nearby.

Sign-off card lives behind the till: till, espresso, pour-over, closing clean, opening clean, emergency basics from the [[Emergency procedures]] folder. Nobody works a full solo shift before every box is ticked. Patience first - Ana's words, and they are printed on the top of the card.""",
"people/keyholders.md": f"""# Keyholders

Three sets of keys: mine, Ana's, and the spares in the safe. Ana opens Tuesday to Friday; I open Monday, Marcus holds the weekend opens and is not a key holder yet - he gets his set after the winter probation per the [[Staff rota]] plan.

The alarm code rotates every quarter and after any staff departure; the code sheet lives in the safe, not on a phone. Opening duties: alarm off, lights, machine on, till float counted, then the delivery checks if it is a Tuesday or Friday - the routine is printed on the inside of the office door next to the [[Emergency procedures]] summary.""",
# ---------------- customers ----------------
"customers/regulars.md": f"""# Regulars

Mr. Okafor, flat white, every weekday at 7:15 - started coming during the 2023 renovation and has not missed a week since. Dr. Lena with the border collie Juno, Saturdays 9:00, oat cortado, dog gets the biscuit jar. The Thursday chess pair, Tom and Ravi, take the window table 16:00 to 18:00 and order batch brews until closing.

Names on cups for anyone who orders three times a week or more - the [[Loyalty scheme]] tracks the count automatically. Regulars' tabs are never run on credit; the card system handles everything, per the [[Accounting basics]] rules Priya set.""",
"customers/loyalty-scheme.md": f"""# Loyalty scheme

Digital punch card through the till: tenth drink free, capped at the drink's base price, no upsizing on the freebie. Sign-up happens at the till in fifteen seconds - name and phone number, nothing else. We hold about 1,900 active cards as of October 2026.

Redemption rate sits at 61 percent, which is high; the free-drink cap keeps the cost predictable and it shows in the [[Cashflow Q4]] sheet as a fixed small line, not a leak. The punch card doubles as the regulars counter - three visits in a week pings the till so bar staff start putting names on cups, per the [[Regulars]] custom.""",
"customers/wholesale-clients.md": f"""# Wholesale clients

Four office accounts as of November 2026: the architects on Priors Lane (2kg weekly), the dental practice, the co-working floor on Bell Court (5kg weekly, biggest line), and the bookshop cafe which takes 3kg of the Ethiopian. Deliveries go out Mondays, roasted the previous Wednesday per the [[Roasting profiles]] schedule so every bag arrives rested.

Wholesale price is $19/kg on the Brazilian blend, $24/kg on singles, net 14 days. Contracts renew each January. The co-working floor asked about a branded bag in 2027 - promising lead, but the [[Business goals]] note says wholesale stays under a third of revenue so the shop keeps its soul.""",
"customers/feedback-log.md": f"""# Feedback log

Running note, newest last. 2026-10-02: a guest found the iced latte too milky - changed the build order to ice first, then milk to half, then espresso. 2026-10-14: three compliments in one week for the cardamom cortado; it earns its [[Seasonal menu]] slot. 2026-11-01: a complaint about weekend queue times over ten minutes; answer is the second grinder, not more staff - costed in the [[Business goals]] note.

The sparkling-water-on-the-saucer habit from the [[Espresso menu]] remains the most-mentioned line of praise. Every change made from this log gets a date and a sentence on what changed.""",
# ---------------- finance ----------------
"finance/accounting-basics.md": f"""# Accounting basics

Priya Shah, our accountant, closes the books monthly - statements and VAT go digital through the Making Tax Digital portal. VAT registration threshold crossed in early 2025; we file quarterly. Card settlements land two working days after the till batch, so Friday's takings show Monday - Priya reconciles them against the Z-reports I photograph nightly.

Rules she set that keep us clean: no cash credit for anyone including [[Regulars]], every supplier invoice photographed the day it arrives ([[Harbor Green deal]] invoices on Tuesdays and Fridays), and the owner drawings stay on the fixed monthly schedule, not "when it looks good".""",
"finance/cashflow-q4.md": f"""# Cashflow Q4

Q4 2026 projection: revenue averages 41k a month across the quarter, boosted by the [[Events calendar]] cuppings and wholesale renewals in January. Fixed lines: rent per the [[Rent agreement]], Priya's fee, [[Insurance]], Dairy Fresh and Flour Lane weekly, Harbor Green every second week - the rhythm matches the [[Suppliers beans]] delivery days.

Green stock floor is two weeks of roast capacity; below that we prepay a Roastworks backup order, which hurts, which is why the floor exists. December's cardamom bun margin is the year's best - [[Pastry supplier]] Flour Lane holds our price if we commit to volume by 15 November. Drawings stay fixed through January, no exceptions.""",
"finance/rent-agreement.md": f"""# Rent agreement

The lease on 14 Priors Lane runs to June 2028, renewal notice window opens December 2027 - put it in the calendar now. Rent is 2,850 a month, reviewed annually each April; the 2026 review passed without uplift because the landlord wants the roastery theatre staying - the glass partition gets mentioned in every negotiation, so keep it spotless on [[Roasting profiles]] days.

Service charge covers the facade and the shared yard; our storage basement is included. Any signage change needs landlord consent in writing - the planned window vinyl for the winter menu needs that letter before the printer is called, per the [[Marketing plan]].""",
"finance/insurance.md": f"""# Insurance

Policy covers public liability, employer's liability, product liability, and the roasting equipment on an all-risks basis; renewal is each 1 March and the quote cycle starts six weeks out. The insurer's surveyor asked for the [[Emergency procedures]] folder and the [[Equipment maintenance]] log at the last review - both impressed him, keep them current.

Any incident goes in the accident book the same day and to Priya the same week; claims history is why the premium has not moved in two years. The co-working floor account on the [[Wholesale clients]] list asked for our liability certificate - a copy lives in the shared drive and one in the blue binder.""",
"finance/business-goals.md": f"""# Business goals

2027, in order: first, the second grinder to kill the weekend queue complaint from the [[Feedback log]] - costed at 2,400, pays for itself in Saturday throughput. Second, wholesale stays under a third of revenue so the shop keeps its character even as the [[Wholesale clients]] book grows. Third, Ana's roasting development continues - she takes the Thursday [[Cupping notes]] chair from January.

Fourth: a web shop for subscription bags, quietly, after the tax year - see [[Website notes]]. Fifth: the [[Cashflow Q4]] floor discipline stays. What we are NOT doing: a second site, delivery apps, and any discounting that erases the sparkling-water margin.""",
# ---------------- marketing ----------------
"marketing/marketing-plan.md": f"""# Marketing plan

Three channels, honestly kept: the [[Instagram strategy]], the monthly cupping events, and the pavement A-board which sells more than it should. No paid ads this year - word of mouth and the [[Regulars]] carry us, and the [[Loyalty scheme]] does the retention quietly.

Seasonal menu boards get wiped the first Monday of the new season - nothing lingers. The winter window vinyl needs the landlord letter from the [[Rent agreement]] process before printing. Everything we publish must pass the shop test: would Mr. Okafor roll his eyes at it? If yes, it does not go out.""",
"marketing/instagram-strategy.md": f"""# Instagram strategy

Three posts a week, batched on Sunday evenings: one product, one person, one process. The process reels outperform everything - the September cardamom cortado reel at 40k views was a single take of Ana pouring, no music until the last second. Faces beat latte art close-ups; the throwdown bronze post is still our top follower day.

Never post a price, always post the name; the [[Seasonal menu]] changes get their own teaser the week before. Stories carry the daily reality - roast days with the curtain open per the [[Shop layout]]. Reply to every comment in the shop voice, dry and warm. Cap: thirty minutes a week, or it becomes a job.""",
"marketing/website-notes.md": f"""# Website notes

The site is a single static page: menu, hours matching the [[Opening hours]] note, address, and the wholesale contact form. It loads in under a second and needs nothing clever - the shop is the product. Domain renews in March.

Planned for 2027, after the tax year: the subscription web shop from the [[Business goals]] list - three tiers, roasted Wednesdays, shipped Mondays so bags land rested like the [[Wholesale clients]] get. No chat widget, no tracking pixels; the WiFi password changes monthly and goes on receipts, not on the site.""",
"marketing/events-calendar.md": f"""# Events calendar

Monthly public cupping, last Saturday at 10:00, twelve seats, 15 a head, bookable at the till - Ana chairs it from January per the [[Business goals]], I run it until then using the [[Cupping notes]] format. The September session sold out in four days; the almond danish test from the [[Pastry supplier]] made the pastry case story write itself.

Private cuppings for offices from the [[Wholesale clients]] list run on quiet Friday afternoons during [[Opening hours]] quiet hours. Winter sketch: mulled batch brew launch the first December Saturday, subscription pre-list the week after, nothing bigger until the new year.""",
# ---------------- product ----------------
"product/cupping-notes.md": f"""# Cupping notes

Thursday cupping, 14:00, back table: three cups each of whatever arrived on the Tuesday [[Suppliers beans]] delivery. Score out of ten on sweetness, acidity, body, finish; anything under six does not reach the retail shelf even if the lot was booked.

Current lineup: the Cerrado Brazil scores steady 7.5s - chocolate, almond, forgiving. The Guji washed lot peaked at 8.8 in September; it is what the chess pair drinks on Saturdays. Ana's palate is the reference point for the florals; she catches the tea-note drift two days before anyone else, which matters for [[Bean storage]] decisions.""",
"product/bean-storage.md": f"""# Bean storage

Green coffee lives in GrainPro liners inside the jute, off the floor on pallets in the dry corner of the roastery - the basement floods once a year, so nothing green goes down there, ever. Rotate by delivery: Friday's bags behind Tuesday's, matching the [[Suppliers beans]] rhythm.

Roasted coffee rests 4 days for the Brazilian, 7 for the washed lots before it hits the bar or the shelf - [[Roasting profiles]] explains why the dial moves on delivery days. Retail bags are valve-sealed, roast date stamped, shelf life 28 days then staff discount. The whiteboard above the grinder tracks rest days in chalk; when the chalk is wrong, the [[Grinder settings]] taste wrong.""",
"product/decaf-choice.md": f"""# Decaf choice

Our decaf is the sugarcane EA process Colombian - it tastes like coffee, which is the whole point. Decaf drinkers are 11 percent of takeaways and remember being treated as an afterthought everywhere else; ours gets the same dial-in attention as the mains, see [[Grinder settings]].

It rests faster than caffeinated lots - 3 days is enough, shorter than the [[Bean storage]] standard - and stales faster too, so it roasts in half-batches on Wednesdays with the wholesale run. Order cadence follows the [[Suppliers beans]] Tuesday delivery; Harbor Green carries it as a special line, minimum one bag per drop.""",
}

def main():
    force = "--force" in sys.argv
    existing = [f for r, _d, fs in os.walk(NOTES) for f in fs if f.endswith(".md")] if os.path.isdir(NOTES) else []
    if existing and not force:
        print(f"notes/ already has {len(existing)} markdown notes - refusing to overwrite. "
              f"Use --force to regenerate the sample set.")
        return 1
    count = 0
    for rel, body in N.items():
        path = os.path.join(NOTES, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        count += 1
    print(f"wrote {count} sample notes into {os.path.relpath(NOTES, ROOT)}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
