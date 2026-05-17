-- 1. Profiles table to store user roles
CREATE TABLE profiles (
  id UUID REFERENCES auth.users ON DELETE CASCADE PRIMARY KEY,
  full_name TEXT,
  role TEXT DEFAULT 'driver' CHECK (role IN ('super_admin', 'admin', 'driver')),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Cars table
CREATE TABLE cars (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  license_plate TEXT UNIQUE NOT NULL,
  brand TEXT,
  model TEXT,
  status TEXT DEFAULT 'available' CHECK (status IN ('available', 'rented', 'maintenance')),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Drivers table
CREATE TABLE drivers (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  profile_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
  first_name TEXT NOT NULL,
  last_name TEXT NOT NULL,
  phone TEXT,
  license_number TEXT,
  deposit_balance DECIMAL(10, 2) DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Contracts table
CREATE TABLE contracts (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  car_id UUID REFERENCES cars(id) ON DELETE RESTRICT,
  driver_id UUID REFERENCES drivers(id) ON DELETE RESTRICT,
  rental_type TEXT NOT NULL CHECK (rental_type IN ('full_day', 'shift_12h')),
  promotion_type TEXT DEFAULT 'none' CHECK (promotion_type IN ('none', '2_free_1', '3_free_1', '5_free_1', '7_free_1')),
  start_date DATE NOT NULL,
  end_date DATE, -- NULL if ongoing
  daily_rate DECIMAL(10, 2) NOT NULL,
  status TEXT DEFAULT 'active' CHECK (status IN ('active', 'closed')),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. Payments table (Receipts)
CREATE TABLE payments (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  contract_id UUID REFERENCES contracts(id) ON DELETE CASCADE,
  amount DECIMAL(10, 2) NOT NULL,
  payment_date TIMESTAMPTZ DEFAULT NOW(),
  receipt_no TEXT UNIQUE,
  payment_type TEXT CHECK (payment_type IN ('rent', 'deposit', 'fine')),
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6. Repairs table
CREATE TABLE repairs (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  car_id UUID REFERENCES cars(id) ON DELETE CASCADE,
  repair_type TEXT CHECK (repair_type IN ('taxi', 'general')),
  description TEXT,
  cost DECIMAL(10, 2) NOT NULL,
  repair_date DATE DEFAULT CURRENT_DATE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enable RLS
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE cars ENABLE ROW LEVEL SECURITY;
ALTER TABLE drivers ENABLE ROW LEVEL SECURITY;
ALTER TABLE contracts ENABLE ROW LEVEL SECURITY;
ALTER TABLE payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE repairs ENABLE ROW LEVEL SECURITY;

-- Simple Policies (Can be refined)
-- Profiles: Users can read their own profile
CREATE POLICY "Users can read their own profile" ON profiles FOR SELECT USING (auth.uid() = id);
-- Admins can read all profiles
CREATE POLICY "Admins can read all profiles" ON profiles FOR SELECT USING (
  EXISTS (SELECT 1 FROM profiles WHERE id = auth.uid() AND role IN ('super_admin', 'admin'))
);

-- Cars, Drivers, Contracts, Payments, Repairs: Admins have full access
CREATE POLICY "Admins have full access to cars" ON cars FOR ALL USING (
  EXISTS (SELECT 1 FROM profiles WHERE id = auth.uid() AND role IN ('super_admin', 'admin'))
);
-- ... similar policies for other tables ...
