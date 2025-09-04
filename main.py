import streamlit as st
import requests
import wikipediaapi
from openai import OpenAI
from elevenlabs.client import ElevenLabs
from elevenlabs import save
import os
import json
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
import folium
from streamlit_folium import st_folium
import tempfile
from streamlit_geolocation import streamlit_geolocation

# Load environment variables

# Initialize APIs
openai_client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])



st.set_page_config(page_title="Steepd", layout="wide")

# Initialize session state
if 'current_location' not in st.session_state:
    st.session_state.current_location = None
if 'nearby_places' not in st.session_state:
    st.session_state.nearby_places = []
if 'selected_place' not in st.session_state:
    st.session_state.selected_place = None
if 'story' not in st.session_state:
    st.session_state.story = None
if 'audio_file' not in st.session_state:
    st.session_state.audio_file = None
if 'use_browser_location' not in st.session_state:
    st.session_state.use_browser_location = True
if 'manual_override' not in st.session_state:
    st.session_state.manual_override = False




def get_wikipedia_info(place_name, location=None):
    """Fetch information about a place from Wikipedia with location context"""
    try:
        wiki_wiki = wikipediaapi.Wikipedia(
            language='en',
            extract_format=wikipediaapi.ExtractFormat.WIKI,
            user_agent='CityStoryWalker/1.0'
        )

        # Get location context if available
        search_queries = []
        area = None
        city = None

        if location:
            try:
                geolocator = Nominatim(user_agent="city_story_walker")
                location_info = geolocator.reverse(f"{location[0]}, {location[1]}", language='en')

                if location_info and location_info.raw:
                    address = location_info.raw.get('address', {})
                    area = address.get('suburb') or address.get('neighbourhood') or address.get('district')
                    city = address.get('city') or address.get('town')
            except:
                pass

        # Handle generic names that need context
        generic_names = ['lion', 'lions', 'statue', 'monument', 'fountain', 'cross',
                         'memorial', 'church', 'park', 'garden', 'square', 'bridge']

        name_lower = place_name.lower()
        needs_context = any(generic in name_lower for generic in generic_names)

        # For landmarks in Trafalgar Square area
        if "lion" in name_lower and location and 51.507 < location[0] < 51.509 and -0.129 < location[1] < -0.127:
            search_queries = ["Trafalgar Square Lions", "Landseer Lions"]
        # Build search queries based on context
        elif needs_context and (area or city):
            if area and city:
                search_queries = [
                    f"{place_name} {area} {city}",
                    f"{place_name} {area}",
                    f"{place_name} {city}",
                ]
            elif area:
                search_queries = [f"{place_name} {area}"]
            elif city:
                search_queries = [f"{place_name} {city}"]
        else:
            # For non-generic names, try with location context first
            if area:
                search_queries.append(f"{place_name}, {area}")
            if city and city != area:
                search_queries.append(f"{place_name}, {city}")
            search_queries.append(place_name)

        # Try each search query
        for query in search_queries:
            # First try direct page lookup
            page = wiki_wiki.page(query)

            if page.exists():
                # Check if the content is relevant (not about animals if looking for statues, etc)
                content = page.text[:500].lower()

                # Skip if we're looking for a place but got an animal/generic article
                if needs_context:
                    skip_terms = ['species', 'genus', 'animal', 'mammal', 'carnivore', 'biology']
                    if any(term in content for term in skip_terms):
                        continue

                full_content = page.text[:2000] if len(page.text) > 2000 else page.text
                return {
                    'title': page.title,
                    'content': full_content,
                    'url': page.fullurl
                }

            # If direct lookup fails, try search API
            search_url = "https://en.wikipedia.org/w/api.php"
            search_params = {
                'action': 'opensearch',
                'search': query,
                'limit': 5,
                'format': 'json'
            }

            response = requests.get(search_url, params=search_params)
            if response.status_code == 200:
                data = response.json()
                if len(data) > 1 and len(data[1]) > 0:
                    for suggested_title in data[1]:
                        # Skip generic results for place-specific searches
                        if needs_context:
                            title_lower = suggested_title.lower()
                            if any(skip in title_lower for skip in ['species', 'genus', 'biology']):
                                continue

                        page = wiki_wiki.page(suggested_title)
                        if page.exists():
                            content = page.text[:500].lower()

                            # Verify relevance for generic names
                            if needs_context:
                                skip_terms = ['species', 'genus', 'animal', 'mammal', 'carnivore', 'biology']
                                if any(term in content for term in skip_terms):
                                    continue

                            full_content = page.text[:2000] if len(page.text) > 2000 else page.text
                            return {
                                'title': page.title,
                                'content': full_content,
                                'url': page.fullurl
                            }

        return None

    except Exception as e:
        st.error(f"Error fetching Wikipedia data: {str(e)}")
        return None


def create_narrative_story(place_info, selected_place=None):
    """Use OpenAI to transform Wikipedia content into an engaging narrative"""
    if not place_info:
        return None

    # Get location context
    location_context = ""
    if selected_place and 'lat' in selected_place and 'lon' in selected_place:
        try:
            geolocator = Nominatim(user_agent="city_story_walker")
            location_info = geolocator.reverse(f"{selected_place['lat']}, {selected_place['lon']}", language='en')

            if location_info and location_info.raw:
                address = location_info.raw.get('address', {})
                area = address.get('suburb') or address.get('neighbourhood') or address.get('district')
                city = address.get('city') or address.get('town', 'London')
                location_context = f"The visitor is currently in {area}, {city}"
        except:
            location_context = "The visitor is currently in London"

    # Check if this is a memorial
    is_memorial = False
    memorial_context = ""
    if selected_place:
        if selected_place.get('type') == 'memorial' or 'memorial' in selected_place.get('name', '').lower():
            is_memorial = True
            memorial_context = f"""
            Important: The visitor is standing at the {selected_place['name']} memorial/monument in {location_context}.
            Frame the story from this perspective - they are AT the memorial, not reading about the person in abstract.
            Connect the person's story to why they are memorialized in THIS specific location.
            """

    prompt = f"""
    Transform the following Wikipedia information about {place_info['title']} into an engaging, 
    narrative-driven story that someone would enjoy hearing while walking past this location. 

    {location_context}
    {memorial_context}

    Make it conversational, interesting, and about 2-3 minutes of speaking time (roughly 300-400 words).
    Include interesting facts, historical context, or amusing anecdotes if available.
    Write it as if you're a knowledgeable local guide talking to a friend who is standing right at this spot.

    If this is about a person who has a memorial here, explain their connection to this area and why they're commemorated here.

    Source information:
    {place_info['content']}

    Create an engaging narrative story that's relevant to someone standing at this location:
    """

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system",
                 "content": "You are a master storyteller who creates engaging narratives about places. You always consider the visitor's current location and frame stories appropriately."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.8,
            max_tokens=500
        )

        return response.choices[0].message.content
    except Exception as e:
        st.error(f"Error creating narrative: {str(e)}")
        return None


def generate_audio_story(text):
    """Convert the story text to speech using ElevenLabs"""
    try:
        # Initialize ElevenLabs client
        client = ElevenLabs(
            api_key=st.secrets["ELEVENLABS_API_KEY"]
        )

        # Generate audio using text_to_speech.convert
        audio = client.text_to_speech.convert(
            text=text,
            voice_id="21m00Tcm4TlvDq8ikWAM",  # Rachel's voice ID
            model_id="eleven_monolingual_v1",
            output_format="mp3_44100_128"
        )

        # Save audio to a temporary file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        save(audio, temp_file.name)

        return temp_file.name
    except Exception as e:
        st.error(f"Error generating audio: {str(e)}")
        return None


def get_nearby_places(lat, lon, radius=1000):
    """Get nearby notable places using OpenStreetMap Overpass API and verify Wikipedia availability"""

    overpass_url = "http://overpass-api.de/api/interpreter"

    # More comprehensive query with proper formatting
    overpass_query = f"""
    [out:json];
    (
      node["name"]["historic"](around:{radius},{lat},{lon});
      node["name"]["tourism"](around:{radius},{lat},{lon});
      node["name"]["amenity"~"place_of_worship|theatre|arts_centre|library|community_centre"](around:{radius},{lat},{lon});
      node["name"]["leisure"~"park|garden"](around:{radius},{lat},{lon});
      node["name"]["building"~"church|theatre|museum"](around:{radius},{lat},{lon});
      node["name"]["memorial"](around:{radius},{lat},{lon});
      node["name"]["man_made"~"monument|memorial"](around:{radius},{lat},{lon});
      way["name"]["historic"](around:{radius},{lat},{lon});
      way["name"]["tourism"](around:{radius},{lat},{lon});
      way["name"]["amenity"~"place_of_worship|theatre|arts_centre|library|community_centre"](around:{radius},{lat},{lon});
      way["name"]["leisure"~"park|garden"](around:{radius},{lat},{lon});
      way["name"]["building"~"church|theatre|museum"](around:{radius},{lat},{lon});
      way["name"]["memorial"](around:{radius},{lat},{lon});
      relation["name"]["historic"](around:{radius},{lat},{lon});
      relation["name"]["tourism"](around:{radius},{lat},{lon});
      relation["name"]["amenity"~"place_of_worship|theatre|arts_centre|library|community_centre"](around:{radius},{lat},{lon});
      relation["name"]["leisure"~"park|garden"](around:{radius},{lat},{lon});
    );
    out center;
    """

    # Commercial chains to exclude
    exclude_terms = [
        'premier inn', 'travelodge', 'holiday inn', 'ibis', 'hilton', 'marriott',
        'tesco', 'sainsbury', 'asda', 'lidl', 'aldi', 'co-op', 'waitrose',
        'mcdonalds', 'burger king', 'kfc', 'subway', 'starbucks', 'costa',
        'boots', 'superdrug', 'lloyds pharmacy', 'hsbc', 'barclays', 'natwest',
        'santander', 'halifax', 'nationwide'
    ]

    potential_places = []
    seen_names = set()

    try:
        response = requests.get(overpass_url, params={'data': overpass_query}, timeout=10)

        if response.status_code == 200:
            data = response.json()

            for element in data.get('elements', []):
                tags = element.get('tags', {})
                name = tags.get('name')

                if not name or name in seen_names:
                    continue

                # Skip commercial chains
                if any(exclude in name.lower() for exclude in exclude_terms):
                    continue

                seen_names.add(name)

                # Get coordinates
                if 'lat' in element and 'lon' in element:
                    elem_lat = element['lat']
                    elem_lon = element['lon']
                elif 'center' in element:
                    elem_lat = element['center']['lat']
                    elem_lon = element['center']['lon']
                else:
                    continue

                # Calculate distance
                distance = geodesic((lat, lon), (elem_lat, elem_lon)).meters

                # Build place info
                place_info = {
                    'name': name,
                    'lat': elem_lat,
                    'lon': elem_lon,
                    'distance': int(distance),
                    'tags': tags  # Store all tags for context
                }

                # Add type information for context
                if 'memorial' in tags or 'man_made' in tags:
                    place_info['type'] = 'memorial'
                elif 'amenity' in tags:
                    place_info['type'] = tags['amenity']
                elif 'tourism' in tags:
                    place_info['type'] = tags['tourism']
                elif 'leisure' in tags:
                    place_info['type'] = tags['leisure']
                elif 'historic' in tags:
                    place_info['type'] = 'historic'
                elif 'building' in tags:
                    place_info['type'] = tags['building']

                potential_places.append(place_info)

            # Sort by distance
            potential_places.sort(key=lambda x: x['distance'])

    except Exception as e:
        st.error(f"Error with Overpass API: {str(e)}")

    # Now verify which places have Wikipedia articles
    places_with_wiki = []

    with st.spinner("Checking for available stories..."):
        for place in potential_places[:20]:  # Check up to 20 places
            wiki_info = get_wikipedia_info(place['name'], location=(place['lat'], place['lon']))
            if wiki_info:
                place['has_wiki'] = True
                place['wiki_title'] = wiki_info['title']
                places_with_wiki.append(place)

                # Stop after finding 8 places with Wikipedia articles
                if len(places_with_wiki) >= 8:
                    break

    return places_with_wiki
def create_map(center_lat, center_lon, places=None):
    """Create an interactive map with the current location and nearby places"""
    m = folium.Map(location=[center_lat, center_lon], zoom_start=15)

    # Add current location marker
    folium.Marker(
        [center_lat, center_lon],
        popup="You are here",
        tooltip="Current Location",
        icon=folium.Icon(color='red', icon='user')
    ).add_to(m)

    # Add nearby places
    if places:
        for place in places:
            folium.Marker(
                [place['lat'], place['lon']],
                popup=place['name'],
                tooltip=f"{place['name']} ({place['distance']}m away)",
                icon=folium.Icon(color='blue', icon='info-sign')
            ).add_to(m)

    return m


# Streamlit UI
st.title("🚶 Steepd Prototype")
st.markdown("Discover the stories behind the places you pass")


# Add custom HTML/JS for geolocation
st.markdown("""
<script>
function getLocation() {
    if (navigator.geolocation) {
        navigator.geolocation.getCurrentPosition(
            function(position) {
                const lat = position.coords.latitude;
                const lon = position.coords.longitude;

                // Send to Streamlit via query params
                const params = new URLSearchParams(window.location.search);
                params.set('lat', lat);
                params.set('lon', lon);
                window.location.search = params.toString();
            },
            function(error) {
                console.error("Error getting location:", error);
            }
        );
    } else {
        alert("Geolocation is not supported by this browser.");
    }
}
</script>
""", unsafe_allow_html=True)

# Check for location in URL params
query_params = st.query_params
if 'lat' in query_params and 'lon' in query_params:
    try:
        auto_lat = float(query_params['lat'])
        auto_lon = float(query_params['lon'])
        if 'location_set' not in st.session_state:
            st.session_state.current_location = (auto_lat, auto_lon)
            st.session_state.location_set = True
            st.session_state.nearby_places = get_nearby_places(auto_lat, auto_lon)
    except:
        pass

# Sidebar for controls
with st.sidebar:
    st.header("📍 Location Settings")

    # Toggle for location source
    location_mode = st.radio(
        "Location Source:",
        ["📍 Browser Location", "✏️ Manual Entry"],
        key="location_mode"
    )

    if location_mode == "📍 Browser Location":
        st.session_state.manual_override = False

        # Get browser location
        st.write("Click below to share your location:")
        location = streamlit_geolocation()

        if location and location['latitude'] is not None:
            new_location = (location['latitude'], location['longitude'])

            # Only update if location changed significantly
            if st.session_state.current_location != new_location:
                st.session_state.current_location = new_location
                st.session_state.nearby_places = get_nearby_places(
                    location['latitude'],
                    location['longitude']
                )

        if st.session_state.current_location:
            st.success(
                f"📍 Location: {st.session_state.current_location[0]:.4f}, {st.session_state.current_location[1]:.4f}")

    else:  # Manual Entry mode
        st.session_state.manual_override = True

        col1, col2 = st.columns(2)
        with col1:
            lat = st.number_input(
                "Latitude",
                value=st.session_state.current_location[0] if st.session_state.current_location else 51.5074,
                format="%.4f",
                key="manual_lat"
            )
        with col2:
            lon = st.number_input(
                "Longitude",
                value=st.session_state.current_location[1] if st.session_state.current_location else -0.1278,
                format="%.4f",
                key="manual_lon"
            )

        if st.button("📍 Set Location", type="primary"):
            st.session_state.current_location = (lat, lon)
            st.session_state.nearby_places = get_nearby_places(lat, lon)
            st.success(f"Location set: {lat:.4f}, {lon:.4f}")

        if st.session_state.current_location:
            st.info(f"📍 Using: {st.session_state.current_location[0]:.4f}, {st.session_state.current_location[1]:.4f}")

    # Refresh button
    if st.session_state.current_location:
        if st.button("🔄 Refresh Nearby Places"):
            st.session_state.nearby_places = get_nearby_places(
                st.session_state.current_location[0],
                st.session_state.current_location[1]
            )

    st.divider()

    # Place selection
    if st.session_state.nearby_places:
        st.header("🏛️ Nearby Places")
        for place in st.session_state.nearby_places:
            if st.button(f"📖 {place['name']}", key=place['name']):
                st.session_state.selected_place = place

                # Fetch Wikipedia info with location context
                with st.spinner("Fetching information..."):
                    wiki_info = get_wikipedia_info(
                        place['name'],
                        location=(place['lat'], place['lon'])
                    )

                if wiki_info:
                    # Generate narrative with location context
                    with st.spinner("Creating your story..."):
                        story = create_narrative_story(wiki_info, selected_place=place)
                        st.session_state.story = story

                    # Generate audio
                    if story:
                        with st.spinner("Generating audio narration..."):
                            audio_file = generate_audio_story(story)
                            st.session_state.audio_file = audio_file
                else:
                    st.warning("No Wikipedia information found for this place.")

    st.divider()

    # Manual place search
    st.header("🔍 Search for a Place")
    manual_place = st.text_input("Enter a place name")
    if st.button("Search"):
        if manual_place:
            with st.spinner("Searching..."):
                wiki_info = get_wikipedia_info(manual_place)

            if wiki_info:
                st.session_state.selected_place = {'name': wiki_info['title']}

                with st.spinner("Creating your story..."):
                    story = create_narrative_story(wiki_info)
                    st.session_state.story = story

                if story:
                    with st.spinner("Generating audio narration..."):
                        audio_file = generate_audio_story(story)
                        st.session_state.audio_file = audio_file
            else:
                st.error("No information found for this place.")

col1, col2 = st.columns([1, 1])

with col1:
    st.header("🗺️ Map View")
    if st.session_state.current_location:
        m = create_map(
            st.session_state.current_location[0],
            st.session_state.current_location[1],
            st.session_state.nearby_places
        )
        st_folium(m, width=500, height=400)
    else:
        st.info("Set your location in the sidebar to see the map")

with col2:
    st.header("📚 Story")
    if st.session_state.selected_place:
        st.subheader(f"**{st.session_state.selected_place['name']}**")

        if st.session_state.story:
            st.write(st.session_state.story)

            if st.session_state.audio_file:
                st.audio(st.session_state.audio_file, format='audio/mp3')


        else:
            st.info("Story is being generated...")
    else:
        st.info("Select a place from the sidebar to hear its story")

# Footer
st.divider()
st.markdown("---")
st.markdown("*Powered by Wikipedia, OpenAI, and ElevenLabs*")