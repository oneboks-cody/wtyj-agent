"""Owner-requested editorial copy, pinned to observed source facts. Offline only."""
import json
from pathlib import Path

SUMMARIES = {
 'afternoon-explorer-tour': 'There is a whole other island beneath the surface 🐠\n\nSet out from Playa Daaibooi in a glass-bottom boat and watch reefs and fish come into view below you. A little afternoon discovery, with snorkel gear, snacks and soft drinks included. The trip runs from 13:00 to 14:30.',
 'all-west-beach-hopping': 'Let the beaches set the rhythm of your day 🌴\n\nExplore Piskado, Kenepa and Cas Abao, with a visit to Shete Boka National Park and the St. Willibrordus flamingo area along the way. A lovely mix of beach time and island discovery, with hotel transport included. Wildlife sightings are never guaranteed.',
 'aquafari-curacao-snorkel': 'Ready to discover what lies beneath the water? 🐠\n\nExplore with a powered underwater scooter and a professional diver, opening a different window onto Curaçao. The experience includes a safety briefing and 45 minutes underwater. The listing specifies ages 12 and over.',
 'atv-buggy-3-hours': 'Trade a little beach time for the eastern trails 🌴\n\nRide through the aloe plantation, Sint Joris Bay and Indian Cave, then make time for a swim at Playa Canoa and a visit to Natural Bridge. Three hours that bring riding, exploring and an island swim into one outing.',
 'atv-buggy-4-hours': 'Follow your curiosity onto Curaçao’s western trails.\n\nThis four-hour off-road outing takes in Ricon Park, Hato and San Pedro before continuing to Smiling Rock, Voodoo Cave and Houtje Bay. A fuller taste of the island for guests who would rather explore than stay in one place.',
 'atv-buggy-6-hours': 'Make a day of the trails, then make time for the water 🌊\n\nExplore Hato, San Pedro and San Antonio Cave, with a Daaibooi swim and stops for turtle viewing and flamingos. Six hours of riding and discovery, with wildlife encounters left to nature.',
 'atv-buggy-7-hours': 'Give your adventurous side a whole day out 🌴\n\nFollow western trails and caves, with stops around Ascencion, the Blue Room and Williwood. The seven-hour outing includes a two-hour swimming stop, so there is room for both exploration and time in the water.',
 'curacao-city-highlights': 'Step ashore and start discovering Curaçao.\n\nFrom Willemstad’s neighbourhoods and Chobolobo to viewpoints, the flamingo area and Que Tapa Beach, this four-hour tour offers cruise guests a varied island introduction. Meet at the Mega Pier cruise terminal. Flamingo sightings depend on nature.',
 'discover-scuba-dive': 'Your first look at a reef from beneath the surface can begin here 🐠\n\nStart with theory and shallow-water practice, then visit the reef with a PADI professional. A guided introduction for the curious first-time diver. Medical screening and restrictions on flying after diving apply.',
 'discover-willemstad': 'Let Willemstad unfold, one neighbourhood at a time.\n\nExplore Otrobanda, Scharloo, Fort Waakzaamheid and Chobolobo, then discover Plasa Bieu and Punda’s market. Four hours for guests who enjoy getting to know a place beyond the beach. Hotel transport is included; lunch is extra.',
 'flyboard-experience': 'See Jan Thiel Beach from a very different angle 🌊\n\nTry the lift and balance of flyboarding, with equipment, instruction and a safety briefing included. The one-hour experience advertises 30 to 40 minutes of flight time. An energetic choice for guests looking to try something new.',
 'half-day-snorkel-lunch': 'A little sailing, a little snorkelling, then lunch aboard 🌊\n\nCruise Spanish Water and the south coast by catamaran, explore the Tugboat snorkelling site and a shallow reef, and settle in for a BBQ lunch. Fresh fruit, snorkel gear and an open bar round out the outing.',
 'jetski-tour-30-min': 'Take your island adventure onto the water 🌊\n\nAfter an operating tutorial, join a guided jet-ski ride along the coast past the Sea Aquarium and Mambo Beach. A lively 30-minute outing, with a life jacket included. Arrive early to complete the paperwork.',
 'kadushi-utv-tours': 'Rocky trails first. A beach swim to finish 🌴\n\nDrive past windmills, Indian Cave and Boca Patrick before stopping at Kokomo Beach for time in the water. This three-hour UTV outing brings a little off-road energy to a day on the island.',
 'klein-curacao-a-trip-to-paradise': 'Picture a day built around an island, the sea and time to unwind 🌊\n\nMermaid’s boat trip takes you to Klein Curaçao, an island off Curaçao, with breakfast, BBQ lunch, snorkel gear and beach-house facilities included. Beach beds give you somewhere to settle between exploring and relaxing. Published departure: 06:45 from Fisherman’s Pier.',
 'klein-curacao-catamaran-day-trip': 'Let the journey become part of your island day 🌊\n\nTravel by catamaran to Klein Curaçao for beach time and snorkelling, with breakfast and BBQ lunch along the way. The listing includes beach beds and an open bar. A full-day choice for guests drawn to the sea and an unhurried island escape.',
 'klein-curacao-weekly-luxury-yacht-trip': 'Make room in your holiday for an island day aboard Serendipity 🌊\n\nTravel by yacht to Klein Curaçao, where beach-house facilities, beach beds and a guided snorkel safari await. Breakfast and BBQ lunch are included. The advertised crossing is about one hour; actual conditions can affect the journey.',
 'mid-day-reef-tour': 'Look down and discover a different side of Curaçao 🐠\n\nFrom Playa Daaibooi, a glass-bottom boat brings reefs and marine life into view beneath you. Snorkel gear, snacks and soft drinks are included. The 11:00 to 12:30 outing leaves the rest of your day open to island time.',
 'miss-ann-klein-curacao': 'Give your weekend a little island escape 🌴\n\nThe Serendipity yacht’s Weekend Supreme package takes you to Klein Curaçao, with beach-house facilities, beach beds and a guided snorkel safari. Breakfast, BBQ lunch and an open bar are included, leaving you to choose how to enjoy your island day.',
 'national-park-jeep-safari': 'Take your island adventure beyond the beach 🌴\n\nHop into an open vehicle and explore Curaçao’s north coast, Jan Kok and Christoffel National Park. A day for curious explorers who want to see more of the island, with bottled water and entrance fees included. The trip runs from 09:00 to 15:00; lunch is extra.',
 'open-fishing-trip': 'Head out with a fishing crew and see what the day brings 🌊\n\nJoin a shared trip targeting wahoo, barracuda, tuna and mahi mahi, with fishing equipment and drinks included. If there is a catch, the crew cleans and packages it afterwards. Four hours on the water, with no catch guaranteed.',
 'seatrek-underwater-walking-tour': 'Imagine exploring the underwater world on foot 🐠\n\nWalk among corals and fish on this one-hour experience, with water shoes, bag storage and a public shower included. A different way to discover life beneath the surface. Optional photos, video and hotel transport cost extra.',
 'snorkel-and-beach-adventures': 'An island adventure with time to slip into the sea 🌊\n\nTake a 4×4 to Shete Boka National Park, snorkel at Playa Piskado beach, then enjoy beach time at Cas Abao. From exploring on land to looking beneath the surface, the 09:00 to 15:00 outing brings both sides of Curaçao together.',
 'sunset-boat-trip': 'Let the evening take you out onto the water 🌊\n\nLeave Playa Daaibooi aboard a glass-bottom boat to take in the coastline and look for marine life below. With snacks and soft drinks included, this 18:00 to 19:30 outing offers a gentle way to bring your island day to a close.',
 'sunset-sail': 'Picture yourself aboard a catamaran as day gives way to evening 🌊\n\nSail along Curaçao’s coast at sunset, with finger food and an open bar included. A lovely choice for guests who want time together on the water. Departure time and optional hotel transport charges need confirmation.',
 'sup-curacao-paddle-boarding': 'Find your own rhythm, one paddle stroke at a time 🌊\n\nHead out with a guide in a small group of one to four, with instruction and SUP equipment included. Guests aged 12 and over can use their own board; younger children share an adult’s board at no extra charge under the published rules.',
 'tandem-skydive-experience': 'A different kind of island adventure begins at 10,000 feet.\n\nThis tandem skydive combines approximately 45 seconds of freefall with around seven minutes under the parachute. Briefing, instructor guidance and equipment are included. Photo and video packages are optional extras.',
 'the-jungle-tour': 'Follow your curiosity into Amazonia 🐒\n\nDiscover its reptiles, birds and monkeys on a 45 to 60-minute animal experience. An outing for guests who enjoy taking a closer look at the living world. Admission varies by age; cancellation terms need clarification before booking.',
 'tugboat-seabob': 'A wreck, corals and a new way to explore beneath the surface 🐠\n\nUse a Seabob to discover the Tugboat wreck, nearby corals and a dive wall in a small group of two or three. Snorkelling gear and a safety briefing are included in this one-hour underwater outing.',
 'west-coast-blue-room': 'Let the west coast fill your day with discovery 🌊\n\nTravel by catamaran, stop at beaches, snorkel and enjoy BBQ lunch, then return by open-air bus. The Blue Cave adds another possibility to the outing, with entry always dependent on conditions. A full day for guests who love a mix of coast and sea.',
 'wonders-of-curacao': 'From caves to beach time, discover the island’s variety 🌴\n\nVisit Hato Caves, snorkel at Playa Piskado beach and unwind at Porto Marie, with a stop at the St. Willibrordus flamingo area. Hotel transport is included. A seven-hour outing for curious guests, with wildlife sightings left to nature.',
}

EXTRAS = {
 'all-west-beach-hopping':'Hotel transport is included.',
 'discover-willemstad':'Hotel transport is included. Lunch is extra.',
 'half-day-snorkel-lunch':'Optional extras and any additional charges need confirmation.',
 'jetski-tour-30-min':'Please arrive early for the operating tutorial and paperwork. Transfer arrangements and any extra charges need confirmation.',
 'klein-curacao-a-trip-to-paradise':'Hotel transfers, an open bar or individual drinks, and massage cost extra. Scuba diving is advertised as available; arrangements and prices need confirmation.',
 'klein-curacao-catamaran-day-trip':'Optional extras and any additional charges need confirmation.',
 'klein-curacao-weekly-luxury-yacht-trip':'Open bar, massage, diving and hotel transfers cost extra.',
 'miss-ann-klein-curacao':'Massage, diving and hotel transfers cost extra.',
 'national-park-jeep-safari':'Lunch is extra. The listing excludes guests who are pregnant or have back problems.',
 'seatrek-underwater-walking-tour':'Underwater photos or video and hotel pickup or drop-off cost extra; prices need confirmation.',
 'snorkel-and-beach-adventures':'Guests who swim or snorkel must be able to swim. The listing excludes guests who are pregnant or have back problems.',
 'sunset-sail':'Hotel transport is available at an additional charge; price and departure time need confirmation.',
 'sup-curacao-paddle-boarding':'Optional extras carry an additional charge; arrangements and prices need confirmation.',
 'tugboat-seabob':'Photo and video options cost extra; prices need confirmation.',
 'west-coast-blue-room':'Optional extras and any additional charges need confirmation. Blue Cave entry depends on conditions.',
 'wonders-of-curacao':'Hotel transport is included.',
}

root=Path(__file__).resolve().parents[2]
catalog=json.loads((root/'clients/mermaid/config/isluno_catalog.json').read_text())
path=root/'clients/mermaid/config/isluno_profile.json'
profile=json.loads(path.read_text())
assert set(SUMMARIES)=={p['id'] for p in catalog['products']}
copies={}
for product in catalog['products']:
    key=product['id'];claims=product.get('source_claims',{})
    facts={'summary':SUMMARIES[key]}
    if claims.get('additional_information'):
        assert key in EXTRAS,key
        facts['additional_information']='Good to know: '+EXTRAS[key]
    if claims.get('guarantees'):
        facts['guarantees']='Cancellation: the listing advertises free cancellation up to 24 hours before the experience starts (local time) and a reservation deposit. Final terms need confirmation.'
    if key=='the-jungle-tour':
        facts['guarantees']='Cancellation: this listing contains conflicting terms, including non-refundable tickets. The applicable policy needs confirmation before you commit.'
    for n,value in enumerate(product.get('inclusions') or []):
        value=value.replace('Snorkle','Snorkel').replace('our beach house','the beach house').replace('Boatride','Boat ride').replace('BBQ Lunch','BBQ lunch').replace('Tour Guide','Tour guide')
        facts['inclusion_'+str(n)]='Included: '+value
    for n,value in enumerate(claims.get('metadata') or []):
        if value.endswith(' hours of duration'):
            number=float(value.split()[0]);minutes=int(number*60)
            value=(str(minutes)+' minutes') if minutes<60 else (str(minutes//60)+' hour'+('s' if minutes//60!=1 else '')+((' '+str(minutes%60)+' minutes') if minutes%60 else ''))
            value='Duration: '+value
        elif ':' not in value:value='Area: '+value
        facts['metadata_'+str(n)]=value
    copies[key]={'source_sha256':product['source']['content_sha256'],'facts':facts}
profile['product_copy']=copies
profile['gallery_mode']='carousel'
profile['profile_version']='isluno-premium-experience-20260909'
profile['hospitality_voice']+=' Write with a vivid travel-documentary sense of curiosity and place: concrete, inviting and quietly confident. Help the guest picture the verified activity, rather than stacking adjectives. Use a short atmospheric opening, accurate experience details, and a personal reason it fits. Do not imitate a named presenter, invent wildlife encounters, guarantee weather, or claim sensory details absent from the source. No long monologues. Guide the guest toward their own desired holiday and a clear next step, without pressure. Use the supplied editorial fact bindings and small photo carousels for trip pitches.'
path.write_text(json.dumps(profile,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'rewritten_products':len(copies),'profile_bytes':path.stat().st_size}))
