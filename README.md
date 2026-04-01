# 🎬 Regal Pro (RC 2.0)

The current version of app is deployed at [Streamlit Community](https://regal-pro.streamlit.app/). Use this if you simply want use the app.

If you want to build or run from source, instructions are at the end of this document.

## 🌟 Key Features
### 🗓️ Smart Multi-Day Scheduler
- 7-Day Sync: Automatically synchronizes showtime data for an entire week across your selected theater and all nearby "neighbor" locations.
- Optimization Strategies for Single-Day Schedules:
	- The Smart Marathon (Best Efficiency): The highest movie count that has the lowest total friction. A "3 movies / 1 hop" might beat "4 movies / 3 hops".
	- The Absolute Marathon (Max Movies): Strictly the highest count, even if it involves 4 hops and 50 miles.
	- The Single-Theater Max: The best itinerary that involves zero travel once you arrive at the first theater.
	- Priority Movie Match: Prioritizes getting #1 and #2 ranked movies into the schedule, regardless of location.
- Optimization Strategies for Mult-Day Schedules:
	- Minimize Days (Greedy Density): Packs your selected movies into the fewest number of trips possible.
	- Maximize Compactness (Efficiency Priority): Prioritizes the most efficient schedules with the shortest gaps and minimal travel, even if spread across more days.
- Optionally enforce Regal Unlimited 91 min gap between shows Rule.
- Optionally enable a Fudge Factor to allow 5 minute overlap between showtimes. This will be used only if scheduler is not otherwise able to create an itinerary.
- Download ICS to import a schedule to your calendar.
- Friction-Aware Scoring: Uses a weight system to balance movie volume against "friction" factors like driving distance, theater hops, and wait times.

### 🔎 Advanced Exploration
- Search for your theater in the **Sidebar** using Zip, Theater Name, Street/City or 4 digit Theater Code. Selecting a theater automatically loads data for all nearby locations.
- Theater Explorer:
	- Now Playing: Detailed daily views 
		- Advanced filtering for screen types (IMAX, RPX, ScreenX), ratings, and time windows, new releases etc.
		- Sort by Movie Title, Showtime or Auditorium
		- Group results by Movie, Auditorium or Full Schedule
	- Playing Nearby: Discover exclusive titles playing at neighbor theaters that aren't available at selected location.
	- Upcoming: Theater-scoped "Upcoming" schedules.
- Movie Explorer: Deep-dive into specific titles with granular format-based scheduling and a list of all scheduled dates for the week.

## 🧠 Scheduling Logic: The "Threshold of Friction"
The app engine uses a **Weighted Hybrid Algorithm**:
- **Volume First:** Each movie is assigned a base value of 250 points, ensuring the engine attempts to fit every selected title.
- **Constraints:** The engine strictly follows your Buffer, Max Gap, and Format preferences.
- **Friction Penalties:** Scores are reduced based on "friction" to ensure the most comfortable experience:
    - **Theater Hops:** -40 points per location change.
	- **Travel Distance:** -2 points per mile.
	- **Gaps: -0.1** points per minute of waiting between movies.
	
## 🛠️ Technical Notes
- **Data Source:** Fetched live from Regal API.
- **Time Zone Sync** App sync timezone based on selected theater. User can manually set UTC offset. This will be used for past shows filter and scheduling.
- **Debug Mode** Advanced Settings allows you to view raw API data. Append ?debug=true to the URL or toggle it in the sidebar to see detailed API logs and request/response payloads. This is critical for identifying why a specific theater might be missing showtimes or formatting data unexpectedly.
- **Force Refresh:** If Regal blocks the request, manually initiate data.

## 🖨️ Printing
Enable **Print View** in the sidebar to remove UI elements for a clean paper schedule.

## ☕ Support the Project
If Regal Pro has helped you catch that perfect triple-feature, consider supporting the developer:
	- [Buy Me a Coffee](https://buymeacoffee.com/riyazusman)

## Run from Source Instructions
### Install Python
1. Download: Go to [python.org/downloads](https://www.python.org/downloads/).
2. Run Installer: Open the downloaded file.
   - CRITICAL STEP: On the first screen of the installer, check the box that says "Add Python to PATH".
3. Install: Click Install Now.

### Download the Project from GitHub
1. Click the green "<> Code" button on top of this page.
2. Select "Download ZIP".
3. Extract: Open your "Downloads" folder, right-click the ZIP file, and select Extract All. Navigate to the extracted folder.
4. Run the Application
	- If you are on Windows, open run_regal.bat.
 	- If you are on Mac/Linux open run_regal.sh. 
5. The executable will check and install required python libraries. Wait for the text to stop scrolling. This usually takes about 30–60 seconds. Installing the libraries are required only for the first run.

### Alternatively Use Git Commands
1. `git clone https://github.com/riyazusman/regal-pro.git`
2. `python -m venv venv`
3. `venv\Scripts\activate`
2. `pip install -r requirements.txt`
3. `streamlit run regal_pro.py`

## Access the App
1. Your web browser should automatically open to a new tab at http://localhost:8501.
2. The Regal Pro interface will appear.
3. To stop the app: Go back to the Terminal and press Ctrl + C on your keyboard.