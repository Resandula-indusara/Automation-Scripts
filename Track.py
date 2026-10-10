import requests
import folium
import webbrowser
import os

ip_input = input("Enter the IP Address to track: ")
ip_address = ip_input.strip()

try:
    url = f"https://ipinfo.io/{ip_address}/json"
    response = requests.get(url)
    data = response.json()

    if "loc" in data:
        lat, lon = map(float, data["loc"].split(","))
        city = data.get("city", "Unknown City")
        country = data.get("country", "Unknown Country")
        isp = data.get("org", "Unknown ISP")

        print(f"\n[+] IP Found in: {city}, {country}")
        print(f"[+] Internet Provider: {isp}")
        print("[+] Generating map...")

        my_map = folium.Map(location=[lat, lon], zoom_start=12)

        folium.Marker(
            [lat, lon],
            popup=f"IP: {ip_address}\nCity: {city}\nISP: {isp}",
            icon=folium.Icon(color="red", icon="info-sign")
        ).add_to(my_map)

        map_file = "ip_location_map.html"
        my_map.save(map_file)

        webbrowser.open('file://' + os.path.realpath(map_file))
        print("[+] Map opened successfully in your browser!")

    else:
        print("[-] Could not find location data for this IP.")

except Exception as e:
    print(f"[-] Error occurred: {e}")

    