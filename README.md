## Commission-Based Sales Appointment Scheduling System (CSASS)

A web-based system to book sales appointments and track commissions, fully database-driven. Designed to automate payroll, ensure data accuracy, and simplify operations.

### Business Goals
* ✅ Accurate Commission Tracking — Flat $50/confirmed booking.
* ✅ Automated Payroll — Weekly, from Friday 12:00 AM to Thursday 11:59 PM.
* ✅ User-Friendly Scheduling — Centralized calendar with real-time availability.

### User Roles & Responsibilities
|Role | Responsibility|
------|---------------
|Sales Support Employee| Book appointments, view personal commissions.|
|Salesman | Manage personal unavailability.|
|Administrator (Admin)|Run payroll, export reports, manage users, audit data.|

### Core Features
#### 📅 Booking System
* Unified calendar view (Zoom & In-Person).
* Click-to-book interface with buffer time logic.
* Client lookup and duplicate detection.
* Automated email confirmation sent to Booking Attendee and Client.
#### 💵 Commission & Payroll
* $50 per confirmed booking. (Admin can change)
* Auto-calculated using the database, based on Friday–Thursday week.
* Admins can export CSV reports and finalize pay periods (locking records).
#### ⚙️ Admin Tools
* Audit trail of all booking changes.
* Role-based access control (RBAC).
* Manage salesmen unavailability (block off time in bulk).

### ✅ Functional Requirements Summary
#### Scheduling
* Real-time calendar with color-coded views.
* Client duplication check.
* Automated reminders.
#### Payroll
* Commission auto-calculation using booking data.
* Payroll export to CSV.
* Pay period locking for finalized records.
#### Admin/Data Control
* Audit trail logging (who, what, when).
* Manage availability.
* RBAC enforcement.

### 🧪 Non-Functional Requirements
|Category | Requirement|
----------|-------------|
|Data Integrity | All data must be in PostgreSQL with proper relationships (foreign keys).|
|Usability | Bookings must be possible within 3 clicks.|
|Performance | Payroll queries complete in under 5 seconds.|
|Reliability | No manual intervention required for reminders or payroll.|