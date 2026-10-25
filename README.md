# Smart Classroom & Timetable Scheduler 🎓

A web-based academic scheduling project designed to simplify college timetable management, organize faculty and classroom allocation, and help reduce scheduling conflicts.

🌐 **Live Demo:** [Smart Classroom & Timetable Scheduler](https://smart-classroom-timetable-llph.onrender.com/)

## 📌 Project Overview

Creating a college timetable manually can be time-consuming because it requires managing faculty availability, classroom capacity, subjects, laboratory sessions, student divisions, and batches.

The **Smart Classroom & Timetable Scheduler** aims to make this process easier through a web-based system with timetable management and scheduling constraints.

This project was developed as an academic mini-project to apply web development, Python programming, database management, and problem-solving concepts to a real-world educational challenge.

## 🎯 Problem Statement

Traditional timetable preparation often involves:

- Conflicts between faculty schedules.
- Classroom and laboratory allocation difficulties.
- Challenges in scheduling practical sessions and student batches.
- Time-consuming manual timetable preparation.
- Difficulty updating schedules when availability changes.

## 💡 Proposed Solution

The system provides a centralized web application for managing academic scheduling information and generating or viewing timetables using the scheduling logic implemented in the project.

The goal is to reduce manual effort, organize academic resources, and help prevent timetable conflicts.

## ✨ Key Features

- **Timetable Management:** Generate or manage academic schedules using the implemented scheduling logic.
- **Faculty Allocation:** Organize teaching assignments and faculty schedules.
- **Classroom Allocation:** Manage classroom assignments for academic sessions.
- **Laboratory Scheduling:** Support practical sessions and batch-based scheduling where configured.
- **Conflict Reduction:** Apply scheduling constraints to help avoid conflicting assignments.
- **Academic Data Management:** Organize relevant timetable information through a database.
- **Web-Based Interface:** Access the application through a browser.
- **Online Deployment:** Access the application using the live demo link.

*Note: The availability of individual features depends on the current deployed implementation.*

## 🛠️ Technologies Used

| Technology | Purpose |
|---|---|
| HTML5 | Web page structure |
| CSS3 | Styling and layout |
| JavaScript | Frontend interactions |
| Python | Backend programming and scheduling logic |
| Flask | Python web framework |
| PostgreSQL | Relational database management |
| SQL | Database schema and queries |
| Git and GitHub | Version control and source-code hosting |
| Render | Web application deployment |

## 🏗️ System Architecture

```text
              User
                |
                v
       Frontend Interface
    HTML + CSS + JavaScript
                |
                v
        Flask Web Backend
              Python
                |
                v
         Scheduling Logic
   Timetable and Resource Constraints
                |
                v
           PostgreSQL
             Database
                |
                v
      Timetable Information
```

## 🚀 Live Demo

Visit the deployed application:

**[https://smart-classroom-timetable-llph.onrender.com/](https://smart-classroom-timetable-llph.onrender.com/)**

The application is hosted on Render. Availability of features depends on the current deployment and database configuration.

## 💻 Run the Project Locally

### Prerequisites

Install the following:

- Python
- PostgreSQL
- Git (optional, for cloning the repository)

### 1. Clone the Repository

Replace `YOUR_GITHUB_USERNAME` and `YOUR_REPOSITORY_NAME` with your actual GitHub details.

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/YOUR_REPOSITORY_NAME.git
```

### 2. Open the Project Folder

```bash
cd YOUR_REPOSITORY_NAME
```

If the project files are inside a subfolder, navigate to the folder containing `app.py` and `requirements.txt`.

### 3. Create a Virtual Environment

On Windows:

```powershell
py -m venv .venv
```

Activate the environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

### 4. Install Dependencies

```powershell
py -m pip install -r requirements.txt
```

### 5. Configure Environment Variables

Configure your local PostgreSQL database connection and Flask secret key using environment variables.

Example PowerShell commands:

```powershell
$env:DATABASE_URL = "postgresql://USERNAME:PASSWORD@localhost:5432/DATABASE_NAME"
$env:SECRET_KEY = "replace-with-a-local-secret"
```

Replace the example values with your actual local configuration. Never commit real passwords, secret keys, or other credentials to GitHub.

### 6. Prepare the Database

Create the PostgreSQL database and initialize the tables using the project's `schema.sql` file or the database initialization process implemented in the application.

### 7. Start the Application

If `app.py` contains a Flask application instance named `app`, run:

```powershell
py -m flask --app app run
```

Alternatively, use the project's `start_project.bat` file if it is configured for your local environment.

Open the local URL displayed in the terminal. The default Flask address is usually:

[http://127.0.0.1:5000/](http://127.0.0.1:5000/)

## 📁 Project Structure

```text
Smart-Classroom-Timetable-Scheduler/
│
├── app.py
├── app.js
├── index.html
├── portal.html
├── portal.js
├── style.css
├── schema.sql
├── requirements.txt
├── start_project.bat
├── frontend images/
├── Smart Classroom & Timetable Scheduler.html
├── Smart Classroom & Timetable Scheduler_files/
└── README.md
```

*The exact folder structure may vary depending on the repository. Include only files and folders that are actually present.*

## 👥 Team Members

Our project was developed collaboratively by the following team members:

| Team Member | Role | Responsibilities |
|---|---|---|
| **Ajit Jamadade** | Database & Research | PostgreSQL database management, SQL, database design, and project research |
| **Parshuram Ghodake** | Frontend Developer | HTML, CSS, JavaScript, and user interface development |
| **Rehan Jamadar** | Backend Developer | Python, Flask, backend logic, and application functionality |
| **Harsh Bhople** | Backend Developer | Backend development, application logic, debugging, and integration |

## 🤝 Team Collaboration

We collaborated to develop the Smart Classroom & Timetable Scheduler to simplify academic timetable management, organize classroom and faculty allocation, and help reduce scheduling conflicts.

Each team member contributed according to their responsibilities, combining frontend development, backend programming, database management, and research to build the project.

## 📚 Learning Outcomes

Through this project, we gained or aimed to gain practical experience in:

- Frontend and backend web development.
- Python programming and Flask application development.
- Relational database design and PostgreSQL connectivity.
- Applying data structures, algorithms, and scheduling constraints.
- Testing, debugging, and deploying a web application.
- Team collaboration and software project development.

## 🔮 Future Enhancements

Potential future improvements include:

- More advanced timetable optimization.
- Improved faculty and classroom availability management.
- Better timetable visualization and reporting.
- Enhanced authentication and role-based access.
- More comprehensive conflict detection and schedule updates.
- Improved timetable modification when faculty availability changes.

## 🙌 Acknowledgement

We thank everyone who supported us during the development of this academic mini-project. This project provided an opportunity to apply our technical knowledge to a practical scheduling problem.

## 📄 License

No open-source license has been specified yet. If you want others to reuse, modify, or distribute this project, choose and add an appropriate license.

---

**Project Name:** Smart Classroom & Timetable Scheduler

**Deployment Platform:** Render

**Live Application:** [Open Project](https://smart-classroom-timetable-llph.onrender.com/)

**Developed By:** Ajit Jamadade, Parshuram Ghodake, Rehan Jamadar, and Harsh Bhople
