import os
import uuid
from dotenv import find_dotenv, load_dotenv
from zep_cloud.client import Zep
from zep_cloud.types import Message
from pydantic import Field
from zep_cloud import EntityEdgeSourceTarget
from zep_cloud.external_clients.ontology import EntityModel, EntityText, EntityInt, EdgeModel


load_dotenv(dotenv_path=find_dotenv())
client = Zep(api_key=os.environ.get("ZEP_API_KEY"))


# Define custom entity types for travel planning assistant
class Person(EntityModel):
    """
    Represents a person other than the user (spouse, family member, friend, travel companion).
    """
    person_name: EntityText = Field(description="The name of the person", default=None)
    age: EntityInt = Field(description="The age of the person", default=None)
    relationship_to_user: EntityText = Field(description="Relationship to the user (spouse, friend, family, colleague)", default=None)
    travel_preferences: EntityText = Field(description="Their travel preferences and interests", default=None)
    special_needs: EntityText = Field(description="Any special needs or requirements for travel", default=None)

class Destination(EntityModel):
    """
    Represents a travel destination (city, country, or region).
    """
    destination_name: EntityText = Field(description="The name of the destination", default=None)
    country: EntityText = Field(description="The country where the destination is located", default=None)
    destination_type: EntityText = Field(description="Type of destination (city, country, region, landmark)", default=None)
    best_season: EntityText = Field(description="Best season to visit this destination", default=None)
    visa_required: EntityText = Field(description="Whether visa is required for US citizens", default=None)

class Accommodation(EntityModel):
    """
    Represents lodging options for travelers.
    """
    accommodation_name: EntityText = Field(description="The name of the accommodation", default=None)
    accommodation_type: EntityText = Field(description="Type of accommodation (hotel, resort, hostel, apartment)", default=None)
    star_rating: EntityInt = Field(description="Star rating or quality level", default=None)
    location: EntityText = Field(description="Location or neighborhood", default=None)
    nightly_rate: EntityInt = Field(description="Average nightly rate in USD", default=None)

class Experience(EntityModel):
    """
    Represents activities, tours, and attractions that travelers can enjoy.
    """
    experience_name: EntityText = Field(description="The name of the experience", default=None)
    experience_type: EntityText = Field(description="Type of experience (tour, activity, attraction, show)", default=None)
    duration: EntityText = Field(description="Duration of the experience", default=None)
    price_per_person: EntityInt = Field(description="Cost per person in USD", default=None)
    skill_level: EntityText = Field(description="Required skill level (beginner, intermediate, advanced)", default=None)

class TravelService(EntityModel):
    """
    Represents travel service providers (airlines, transport companies, restaurants).
    """
    service_name: EntityText = Field(description="The name of the service provider", default=None)
    service_type: EntityText = Field(description="Type of service (airline, restaurant, transport, tour operator)", default=None)
    service_class: EntityText = Field(description="Quality/class level (economy, business, first, budget, luxury)", default=None)
    route_or_location: EntityText = Field(description="Routes served or location", default=None)
    rating: EntityText = Field(description="Customer rating or quality score", default=None)

# Define custom edge types for travel relationships
class Visits(EdgeModel):
    """
    Represents a traveler visiting a destination.
    """
    visit_purpose: EntityText = Field(description="Purpose of the visit (vacation, business, family)", default=None)
    visit_frequency: EntityText = Field(description="How often they visit (first time, occasional, frequent)", default=None)
    travel_dates: EntityText = Field(description="When the visit occurs or occurred", default=None)
    satisfaction_level: EntityText = Field(description="How satisfied they were with the visit", default=None)

class StaysAt(EdgeModel):
    """
    Represents a traveler staying at an accommodation.
    """
    check_in_date: EntityText = Field(description="Check-in date for the stay", default=None)
    check_out_date: EntityText = Field(description="Check-out date for the stay", default=None)
    room_type: EntityText = Field(description="Type of room or accommodation booked", default=None)
    booking_status: EntityText = Field(description="Status of the booking (confirmed, pending, cancelled)", default=None)

class Participates(EdgeModel):
    """
    Represents a traveler participating in an experience.
    """
    participation_date: EntityText = Field(description="Date of participation", default=None)
    group_size: EntityInt = Field(description="Number of people in the group", default=None)
    booking_method: EntityText = Field(description="How it was booked (online, agent, walk-in)", default=None)
    experience_rating: EntityText = Field(description="Rating given by traveler for the experience", default=None)

class Books(EdgeModel):
    """
    Represents a traveler booking a travel service.
    """
    booking_date: EntityText = Field(description="When the booking was made", default=None)
    service_date: EntityText = Field(description="When the service is used", default=None)
    confirmation_code: EntityText = Field(description="Booking confirmation number or code", default=None)
    payment_method: EntityText = Field(description="Method used for payment", default=None)

class Recommends(EdgeModel):
    """
    Represents a travel agent recommending something to a traveler.
    """
    recommendation_reason: EntityText = Field(description="Why this was recommended", default=None)
    confidence_level: EntityText = Field(description="Agent's confidence in the recommendation (high, medium, low)", default=None)
    recommendation_date: EntityText = Field(description="When the recommendation was made", default=None)
    traveler_response: EntityText = Field(description="How the traveler responded to the recommendation", default=None)

# Set the ontology with custom entity and edge types
client.graph.set_ontology(
    entities={
        "Person": Person,
        "Destination": Destination,
        "Accommodation": Accommodation,
        "Experience": Experience,
        "TravelService": TravelService
    },
    edges={
        "VISITS": (
            Visits,
            [EntityEdgeSourceTarget(source="User", target="Destination")]
        ),
        "STAYS_AT": (
            StaysAt,
            [EntityEdgeSourceTarget(source="User", target="Accommodation")]
        ),
        "PARTICIPATES": (
            Participates,
            [EntityEdgeSourceTarget(source="User", target="Experience"),
             EntityEdgeSourceTarget(source="Person", target="Experience")]
        ),
        "BOOKS": (
            Books,
            [EntityEdgeSourceTarget(source="User", target="TravelService")]
        ),
        "RECOMMENDS": (
            Recommends,
            [EntityEdgeSourceTarget(source="User", target="Destination"),
             EntityEdgeSourceTarget(source="User", target="Accommodation"),
             EntityEdgeSourceTarget(source="User", target="Experience"),
             EntityEdgeSourceTarget(source="User", target="TravelService")]
        )
    }
)

first_name = "John"
last_name = "Doe"
email = "john.doe@example.com"
zep_user_role = f"{first_name} {last_name}"
zep_assistant_role = "TravelAssistantBot"
ignore_roles = []

uuid_value = uuid.uuid4().hex[:4]
user_id = "default-graph-advanced-" + uuid_value
client.user.add(
    user_id=user_id,
    first_name = first_name,
    last_name = last_name,
    email=email
)

threads = [
    # Session 1: Italian Food Tour Planning - Showcases initial spicy food preference and past travel
    [
        {"name": zep_user_role, "role": "user", "content": "Hi, I'm John Doe, and I want to plan a culinary tour of Italy with my wife Maria next month. She's 28 and loves cooking authentic Italian dishes. I love spicy food and bold flavors!"},
        {"name": zep_assistant_role, "role": "assistant", "content": "Hello John! Italy is perfect for a culinary adventure with Maria. I love that you enjoy spicy food - Italy has amazing regional spicy dishes, especially in the south. When are you planning to visit this beautiful destination?"},
        {"name": zep_user_role, "role": "user", "content": "We visited Rome about 3 years ago for our anniversary and absolutely loved it, so we want to explore more regions this time. We're thinking September 15-25."},
        {"name": zep_assistant_role, "role": "assistant", "content": "Wonderful! Since you visited Rome before and loved it, I recommend exploring Tuscany and Sicily this time - Sicily especially has amazing spicy cuisine that matches your taste preferences. Let me suggest some destinations and accommodations for you and Maria."},
        {"name": zep_user_role, "role": "user", "content": "That sounds perfect! We need accommodations in Florence and Palermo. Maria wants authentic experiences, not touristy places."},
        {"name": zep_assistant_role, "role": "assistant", "content": "Excellent! For Florence, I recommend Villa San Michele, a luxury boutique hotel accommodation with 4-star rating in the hills above the city. Nightly rate is $380, perfect for couples seeking authentic Tuscan experiences. For Palermo, Sicily, I suggest Hotel Villa Igiea, a historic luxury accommodation right on the coast."},
        {"name": zep_user_role, "role": "user", "content": "Book both hotels for Maria and me. What about flights from Chicago?"},
        {"name": zep_assistant_role, "role": "assistant", "content": "Hotels booked! Villa San Michele in Florence check-in September 15th, check-out September 20th, deluxe room type, booking status confirmed. Villa Igiea in Palermo check-in September 20th, check-out September 25th. For flights, Lufthansa offers excellent service to Italy - flight LH441 departs Chicago September 15th, economy class for two is $2400."},
        {"name": zep_user_role, "role": "user", "content": "Perfect! Book those flights for Maria and me. What cooking experiences can we do together?"},
        {"name": zep_assistant_role, "role": "assistant", "content": "Flights booked! Confirmation code LH789123, booking date today, service date September 15th, payment method credit card. For cooking experiences, I recommend the Tuscan Farm Cooking Class in Florence - 6-hour duration, $180 per person, beginner skill level. You and Maria will learn authentic pasta making and local spicy sauces."},
        {"name": zep_user_role, "role": "user", "content": "Book that for September 17th for both of us. What about Sicily - any spicy food experiences there?"},
        {"name": zep_assistant_role, "role": "assistant", "content": "Tuscan cooking class booked for September 17th! Participation date set, group size 2 (you and Maria), booking method online. In Sicily, I highly recommend the Spicy Sicilian Street Food Tour - 4-hour experience, $120 per person, showcasing authentic spicy dishes like arancini with 'nduja and spicy caponata that you'll love!"},
        {"name": zep_user_role, "role": "user", "content": "That sounds amazing! Book the spicy food tour for Maria and me on September 22nd."},
        {"name": zep_assistant_role, "role": "assistant", "content": "Spicy Sicilian food tour booked for September 22nd! Participation date set, group size 2, booking method through agent service. This will be perfect for your love of spicy flavors and Maria's interest in authentic cuisine."},
        {"name": zep_user_role, "role": "user", "content": "Where should we dine for special occasions? We want the best authentic Italian restaurants."},
        {"name": zep_assistant_role, "role": "assistant", "content": "For fine dining, I recommend Osteria di Giovanni in Florence - a luxury restaurant service with traditional Tuscan cuisine and excellent ratings. In Palermo, try Bye Bye Blues, a Michelin-starred restaurant service specializing in modern Sicilian dishes with those bold, spicy flavors you love."},
        {"name": zep_user_role, "role": "user", "content": "Perfect! Reserve both restaurants for Maria and me - Osteria for September 18th and Bye Bye Blues for September 23rd."},
        {"name": zep_assistant_role, "role": "assistant", "content": "Both reservations confirmed! Osteria di Giovanni September 18th at 8:00 PM, confirmation code TUSCAN456. Bye Bye Blues September 23rd at 8:30 PM, confirmation code SICILY789. Payment method credit card, high confidence recommendations for your culinary adventure with Maria!"}
    ],
    # Session 2: Thailand Adventure Trip - Showcases friend relationship and multiple destinations
    [
        {"name": zep_user_role, "role": "user", "content": "Hi again! I want to plan an adventure trip to Thailand with my best friend Jake who's 30. He's really into scuba diving and extreme sports, while I prefer cultural experiences. We're both frequent travelers and love exploring."},
        {"name": zep_assistant_role, "role": "assistant", "content": "Hello John! Thailand is fantastic for combining Jake's adventure sports with your cultural interests. As frequent travelers, you'll appreciate Thailand's diverse destinations. Are you thinking about visiting multiple locations - perhaps Bangkok for culture and southern islands for diving?"},
        {"name": zep_user_role, "role": "user", "content": "Exactly! We want to visit Bangkok first, then Koh Tao for the diving. Jake has advanced diving certification and wants challenging sites, but I'm more interested in the temples and local markets."},
        {"name": zep_assistant_role, "role": "assistant", "content": "Perfect combination! Bangkok offers incredible cultural experiences while Koh Tao is world-renowned for diving. For accommodation, I recommend the Mandarin Oriental Bangkok - luxury hotel with 5-star rating in the cultural district, nightly rate $320. For Koh Tao, Simple Life Resort, a beachfront accommodation perfect for divers, $180 per night."},
        {"name": zep_user_role, "role": "user", "content": "Book both accommodations for Jake and me from November 1-10. Split it - 4 nights Bangkok, 6 nights Koh Tao."},
        {"name": zep_assistant_role, "role": "assistant", "content": "Accommodations booked! Mandarin Oriental Bangkok check-in November 1st, check-out November 5th, deluxe twin room. Simple Life Resort Koh Tao check-in November 5th, check-out November 11th, beachfront bungalow. Both booking status confirmed for you and Jake."},
        {"name": zep_user_role, "role": "user", "content": "Great! What flights can get us there from Los Angeles?"},
        {"name": zep_assistant_role, "role": "assistant", "content": "For flights to Thailand, Thai Airways offers excellent service - flight TG794 departs LAX November 1st at 1:15 AM, arriving Bangkok November 2nd at 6:30 AM local time. Economy class for two passengers is $2800 total with great ratings for international travel."},
        {"name": zep_user_role, "role": "user", "content": "Book those flights for Jake and me. What diving experiences can Jake do in Koh Tao?"},
        {"name": zep_assistant_role, "role": "assistant", "content": "Flights booked! Confirmation code TG567890, booking date today, service date November 1st, payment method credit card. For Jake's diving, I recommend the Advanced Wreck Diving Experience at Koh Tao - full day activity, $150 per person, advanced skill level. Perfect for his certification level and love of extreme sports."},
        {"name": zep_user_role, "role": "user", "content": "Perfect for Jake! Book that for November 7th. What cultural experiences can I do in Bangkok while he's diving?"},
        {"name": zep_assistant_role, "role": "assistant", "content": "Jake's wreck diving booked for November 7th! Participation date set, group size 1, booking method online. For your cultural interests in Bangkok, I recommend the Grand Palace and Temple Tour - 6-hour experience, $80 per person, beginner skill level, covering the most important cultural sites."},
        {"name": zep_user_role, "role": "user", "content": "Book the temple tour for me on November 3rd. What can Jake and I do together?"},
        {"name": zep_assistant_role, "role": "assistant", "content": "Temple tour booked for November 3rd! Participation date set, group size 1, booked through agent service. For shared experiences, I recommend the Thai Cooking Class in Bangkok - 4-hour duration, $90 per person, beginner skill level. Both you and Jake can learn together regardless of your different interests."},
        {"name": zep_user_role, "role": "user", "content": "Perfect! Book the cooking class for both Jake and me on November 4th."},
        {"name": zep_assistant_role, "role": "assistant", "content": "Thai cooking class booked for November 4th! Participation date set, group size 2 (you and Jake), booking method through agent service. This cultural culinary experience will be great bonding time for both of you."},
        {"name": zep_user_role, "role": "user", "content": "What about transport from Bangkok to Koh Tao? Jake wants the fastest option."},
        {"name": zep_assistant_role, "role": "assistant", "content": "For speed, I recommend Nok Air domestic airline service - flight DD123 Bangkok to Koh Samui, then speedboat transfer to Koh Tao. Total journey 3 hours, $180 per person. Much faster than buses and ferries, perfect for Jake's preference for efficient travel."},
        {"name": zep_user_role, "role": "user", "content": "Book that transport for November 5th for both of us."},
        {"name": zep_assistant_role, "role": "assistant", "content": "Transport booked! Flight and speedboat confirmed for November 5th, confirmation code NK456789, booking date today, service date November 5th, payment method credit card. You and Jake will reach Koh Tao quickly for his diving adventures!"}
    ],
    # Session 3: Singapore Business Trip - Showcases business colleague and networking
    [
        {"name": zep_user_role, "role": "user", "content": "I need to plan a business trip to Singapore with my colleague Amanda from our finance department - she's 34 and handles all our Asian market investments. We have important client meetings there."},
        {"name": zep_assistant_role, "role": "assistant", "content": "Hello John! Singapore is excellent for business, especially for finance and investment meetings. As you and Amanda will be representing your company in Asian markets, I recommend staying in the central business district. What dates work for your client meetings?"},
        {"name": zep_user_role, "role": "user", "content": "December 5-10. We need a business hotel with meeting facilities, and Amanda specifically requested a gym since she maintains her fitness routine during business travel."},
        {"name": zep_assistant_role, "role": "assistant", "content": "Perfect! I recommend Marina Bay Sands, a luxury business hotel accommodation with 5-star rating in the financial district. Nightly rate $400 per room, includes business center, meeting rooms, and a world-class gym facility that Amanda will appreciate. Premium location for your client meetings."},
        {"name": zep_user_role, "role": "user", "content": "Excellent! Book that for Amanda and me - separate business rooms. What about flights from Dallas?"},
        {"name": zep_assistant_role, "role": "assistant", "content": "Marina Bay Sands booked! Check-in December 5th, check-out December 10th, two executive business rooms, booking status confirmed for you and Amanda. For flights, Singapore Airlines offers premium business travel service - flight SQ12 departs Dallas December 5th at 8:50 AM, business class for two is $8400."},
        {"name": zep_user_role, "role": "user", "content": "Book those business class flights for Amanda and me. Any networking experiences for finance professionals?"},
        {"name": zep_assistant_role, "role": "assistant", "content": "Flights confirmed! Confirmation code SQ345678, booking date today, service date December 5th, payment method corporate card. For networking, I highly recommend the Singapore Finance Leaders Summit - 6-hour professional experience, $500 per person, expert skill level. Perfect for you and Amanda's investment focus."},
        {"name": zep_user_role, "role": "user", "content": "That sounds ideal for our business goals. Book the summit for both Amanda and me on December 7th."},
        {"name": zep_assistant_role, "role": "assistant", "content": "Finance summit booked for December 7th! Participation date set, group size 2 (you and Amanda), booking method through agent service. This professional networking experience will be valuable for your Asian market investment work."},
        {"name": zep_user_role, "role": "user", "content": "We completed similar business travel to Hong Kong last quarter and it was very successful. What about client entertainment dining in Singapore?"},
        {"name": zep_assistant_role, "role": "assistant", "content": "That's great that your Hong Kong business trip was successful! For client entertainment, I recommend CUT by Wolfgang Puck, a luxury restaurant service with sophisticated atmosphere perfect for business dining. Excellent ratings and ideal for impressing Asian market clients."},
        {"name": zep_user_role, "role": "user", "content": "Perfect! Reserve CUT for our client dinner on December 8th - table for 6 including Amanda, me, and four Singapore clients."},
        {"name": zep_assistant_role, "role": "assistant", "content": "CUT reservation confirmed! Booking date today, service date December 8th at 7:00 PM, confirmation code CUT123456, payment method corporate card. Table for 6, high confidence recommendation for your important client entertainment with Amanda."}
    ],
    # Session 4: Mexico Family Reunion - Showcases spicy food preference change and multiple family members
    [
        {"name": zep_user_role, "role": "user", "content": "Hi! I'm organizing a big family reunion in Mexico with my sister Carmen who's 32, my elderly father Roberto who's 68, and my nephew Luis who's 12. Everyone has different needs and dietary preferences."},
        {"name": zep_assistant_role, "role": "assistant", "content": "Hello John! Mexico is wonderful for family reunions with multiple generations. With Carmen, Roberto, and young Luis, I'll need to consider accessibility for your father and kid-friendly activities for Luis. Which destination in Mexico are you considering for this special family gathering?"},
        {"name": zep_user_role, "role": "user", "content": "We want to visit Cancun and Puerto Vallarta. Roberto has mobility issues but loves history, Carmen enjoys beach activities, and Luis is excited about everything. We need family-friendly accommodations."},
        {"name": zep_assistant_role, "role": "assistant", "content": "Perfect destinations for your multi-generational family! For Cancun, I recommend Moon Palace Resort, a family resort accommodation with 4-star rating, wheelchair accessibility for Roberto, kids' club for Luis, and beach access for Carmen. For Puerto Vallarta, Casa Velas is an adults-preferred resort but welcomes families with excellent facilities."},
        {"name": zep_user_role, "role": "user", "content": "Book both resorts for all four of us - January 10-17. Split the week between both destinations."},
        {"name": zep_assistant_role, "role": "assistant", "content": "Family accommodations booked! Moon Palace Cancun check-in January 10th, check-out January 14th, family suite with accessibility features. Casa Velas Puerto Vallarta check-in January 14th, check-out January 17th, connecting rooms. All booking status confirmed for you, Carmen, Roberto, and Luis."},
        {"name": zep_user_role, "role": "user", "content": "Great! What about flights from Phoenix for all four of us?"},
        {"name": zep_assistant_role, "role": "assistant", "content": "For family travel, American Airlines offers excellent service to Mexico - flight AA234 departs Phoenix January 10th at 11:30 AM, arriving Cancun at 4:20 PM. Economy class for four passengers is $1600 total, includes assistance for Roberto's mobility needs."},
        {"name": zep_user_role, "role": "user", "content": "Book those flights for all of us. Now for dining - here's the thing, I used to love spicy food but I actually don't enjoy spicy cuisine anymore. Can you recommend mild, family-friendly restaurants for all of us?"},
        {"name": zep_assistant_role, "role": "assistant", "content": "Flights booked! Confirmation code AA789012, booking date today, service date January 10th, payment method credit card, special assistance noted for Roberto. I understand you've moved away from spicy food - I'll focus on mild, family-friendly options. I recommend La Isla Restaurant in Cancun, a family restaurant service with mild Mexican cuisine, perfect for all ages and Roberto's preferences."},
        {"name": zep_user_role, "role": "user", "content": "Perfect! That works much better for my current tastes. What cultural experiences can Roberto enjoy with his mobility limitations?"},
        {"name": zep_assistant_role, "role": "assistant", "content": "For Roberto's historical interests with accessibility needs, I recommend the Chichen Itza Accessible Tour - 8-hour cultural experience, $180 per person, wheelchair accessible transportation and modified walking routes. Luis will love the pyramids, and it accommodates Roberto's mobility requirements perfectly."},
        {"name": zep_user_role, "role": "user", "content": "Excellent! Book that tour for all four of us on January 12th."},
        {"name": zep_assistant_role, "role": "assistant", "content": "Chichen Itza accessible tour booked for January 12th! Participation date set, group size 4 (you, Carmen, Roberto, and Luis), booking method through agent service with special accessibility arrangements for Roberto. This cultural experience will be perfect for your family reunion."},
        {"name": zep_user_role, "role": "user", "content": "What can Carmen and Luis do together while Roberto rests?"},
        {"name": zep_assistant_role, "role": "assistant", "content": "For Carmen and Luis, I recommend the Family Snorkeling Adventure in Puerto Vallarta - 3-hour water experience, $120 per person, beginner skill level perfect for 12-year-old Luis. Carmen will enjoy the beach activity and Luis will love the marine life."},
        {"name": zep_user_role, "role": "user", "content": "Perfect! Book the snorkeling for Carmen and Luis on January 15th."},
        {"name": zep_assistant_role, "role": "assistant", "content": "Family snorkeling booked for January 15th! Participation date set, group size 2 (Carmen and Luis), booking method online. This will be a wonderful bonding experience for aunt and nephew while Roberto can relax at the resort."},
        {"name": zep_user_role, "role": "user", "content": "This family reunion is going to be perfect for everyone - Roberto, Carmen, Luis and me. Thank you for accommodating everyone's different needs!"},
        {"name": zep_assistant_role, "role": "assistant", "content": "Wonderful! Your Mexico family reunion includes accessible accommodations and experiences for Roberto, beach activities for Carmen, exciting adventures for Luis, and mild dining options that match your current food preferences. Perfect for bringing four generations together!"}
    ]
]

for thread in threads:
    uuid_value = uuid.uuid4().hex[:4]
    thread_id = "thread-" + uuid_value
    
    client.thread.create(
        thread_id=thread_id,
        user_id=user_id
    )
    
    for m in thread:
        client.thread.add_messages(thread_id=thread_id, messages=[Message(**m)])
