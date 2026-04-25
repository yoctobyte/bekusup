# Bekusup - User Specification

## Overview
"Bekusup" is a conceptual tape-drive style automated rotating backup system. The user has a set of remote hard drives (e.g., sdb, sdc) that act as replaceable 2TB backup media inside a multi-bay system (typically at least a 2-bay configuration).

## Goal
The goal is to copy critical data—primarily the `/home/` directories from various workstations, laptops, and tiny servers, alongside some root-level system configurations (like web server setups)—to these rotating disks in a robust and automated way.

## Core Features and Requirements

1.  **Multiple Sources & Network Resilience**
    *   The system must pull data from various sources over the network, primarily via `ssh`.
    *   Laptops and other nodes might be offline. The system should gracefully fail/skip these unreachable hosts without aborting the broader backup operation.
    *   Servers or other distant sources might have bandwidth caps.

2.  **Authentication & Connectivity**
    *   Must support SSH Key-based authentication.
    *   Must explicitly support `user:pass@host` format, enabling the use of passwords (likely via `sshpass`) if necessary, maximizing flexibility based on user preference.

3.  **Transport Flexibility & Abstracted Providers**
    *   Cannot assume `rsync` is installed on all remote machines. While `rsync` is the preferred tool (and we can advise the user to install it), the system must have fallback mechanisms (such as `scp` or `sftp`).
    *   The architecture must abstract the "transport engine" to eventually support other mediums, such as Windows SMB shares or Cloud Storage (e.g., Google Drive via `rclone`).

4.  **Drive Detection & Labeling**
    *   The user mounts replaceable media that contains the word `backup` in its volume label.
    *   The system must automatically detect these drives.
    *   If the OS has not auto-mounted the drive with sane permissions, the script should be capable of mounting it appropriately or gracefully asking the user for confirmation.

5.  **Data Rotation & Sessions**
    *   Drives are rotated (e.g., swapped daily or randomly).
    *   A single drive may be mounted multiple times over the course of a week to append *new* backup sessions, rather than overwriting old ones.
    *   For the MVP (Minimum Viable Product), every session represents a standalone **Full Backup** of the requested environments. (Incremental network file-level fetching via local disk comparisons is explicitly requested to be a "later feature").

6.  **Expected Technology Stack**
    *   The orchestration script will be written in **Python**.
    *   Configuration details (hosts, passwords, directories) will be loaded from a human-readable schema format, like YAML or JSON.
