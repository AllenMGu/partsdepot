-- PostgreSQL SQLAlchemy Enum(UserRole) stores enum NAMES in userrole.
-- Run before deploying code that introduces supervisor/viewer.
ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'SUPERVISOR';
ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'VIEWER';
