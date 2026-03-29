from flask import Flask, render_template, request, jsonify, send_file
import requests
import re
import csv
import io
from datetime import datetime
import concurrent.futures

app = Flask(__name__)

URL = "https://postings.speechwire.com/r-uil-academics.php?"
YEAR_OFFSET = 2008
MIN_YEAR = 2023
COMPETITIONS = {
    1: "Accounting", 8: "Calculator",
    9: "CS", 3: "Current Events", 10: "Math", 11: "Number Sense",
    12: "Science", 7: "Spelling", 4: "Lit Crit", 6: "Social Studies"
}

def available_years():
    return list(range(MIN_YEAR, datetime.now().year + 1))

def req(params, dist="", reg="", state=""):
    url = f"{URL}groupingid={params['subj']}&Submit=View+Postings&region={reg}&district={dist}&state={state}&conference={params['conf']}&seasonid={params['year']}"
    return requests.get(url, timeout=10).content.decode()

def process_single_district(district_num, params):
    # scrape a single district, return (indiv, team, mxbio, mxchem, mxphys, empty)
    try:
        subj, comp, conf = params['subj'], params['comp'], params['conf']
        scrape = req(params, dist=str(district_num))
        tmp = ("0" if district_num < 10 else "") + str(district_num)
        regex = f"<tr>.*?{tmp}-{conf}A.*?</tr>"
        indiv_places = re.findall(regex, scrape)
        
        indiv_results = []
        team_results = []
        mxbio, mxchem, mxphys = dict(), dict(), dict()
        
        if len(indiv_places) == 0:
            indiv_places = re.findall("<tr>.*?HS.*?</tr>", scrape)
        if len(indiv_places) == 0:
            print(f"NOTHING IN DISTRICT {district_num}, continuing...")
            return None, None, None, None, None, [district_num]
            
        for x in indiv_places:
            values = re.findall("<td class='ddprint centered'>(.*?)</td>", x)
            place = values[0]
            school = values[1]
            name = values[2].strip()
            score = values[-3 if subj % 10 == 1 else -4]
            try:
                score = int(score)
            except:
                score = float(score)
            tup = (score, place, name, school, f"District {district_num}")
            
            if subj == 12:
                bio, chem, phys = int(values[4]), int(values[5]), int(values[6])
                tup = (score, place, name, school, f"District {district_num}", bio, chem, phys)
                if district_num in mxbio: mxbio[district_num] = max(mxbio[district_num], bio)
                else: mxbio[district_num] = bio
                if district_num in mxchem: mxchem[district_num] = max(mxchem[district_num], chem)
                else: mxchem[district_num] = chem
                if district_num in mxphys: mxphys[district_num] = max(mxphys[district_num], phys)
                else: mxphys[district_num] = phys
            indiv_results.append(tup)

        regex = "<tr>(.*?)</tr>"
        team_places = re.findall(regex, scrape)
        for idx in range(len(indiv_places), len(team_places)):
            x = team_places[idx].replace('\u00a0', ' ')
            if x.count("<br>") < 2:  # Team rows have multiple <br> tags for member names
                continue
                
            try:
                place = re.search(r"<td class='ddprint centered'>(.*?)<\/td>", x).group(1).strip()
                school = re.search(r"<td class='ddprint centered'>(.*?)<span", x).group(1).strip()
                school = school[school.rindex('>')+1:].strip()
                regex = r"<td class='ddprint centered'>(-?\d+)<\/td>"
                score, prog_score = 0, 0
                
                if comp == "CS":
                    score = re.search(regex * 2, x).group(2)
                    prog_score = re.search(regex, x).group(1)
                else:
                    score = re.search(regex, x).group(1)
                names = [(N if '<' not in N else N[:N.index('<')]).strip() for N in x.split("<br>")[1:]]
                if names:  # Only add if we found team members
                    team_scores = sorted([tup for tup in indiv_results if tup[2] in names], key=lambda tup: tup[0], reverse=True)
                    team_results.append((int(score), prog_score if comp == "CS" else -999, -999 if len(team_scores) <= 3 else team_scores[3][0], place, school, f"District {district_num}", names))
            except Exception as e:
                print(f"Error processing team row in district {district_num}: {str(e)}")
                continue
            
        return indiv_results, team_results, mxbio, mxchem, mxphys, []
        
    except Exception as e:
        print(f"Error processing district {district_num}: {str(e)}")
        return None, None, None, None, None, [district_num]

def _merge_max(dest, src):
    for k, v in src.items():
        dest[k] = max(dest.get(k, v), v)

def _location_num(location_str):
    # "District 5" -> 5, "Region 2" -> 2
    return int(location_str.split(' ')[1])

def _filter_qualifiers(indiv_results, team_results, subj, mxbio, mxchem, mxphys):
    # filter teams: all 1st + best 2nd (wildcard)
    qualified_teams = []
    wildcard_team = None
    for x in team_results:
        if x[3] == "1st":
            qualified_teams.append(x)
        elif x[3] == "2nd" and wildcard_team is None:
            wildcard_team = x
            qualified_teams.append(x)

    # filter individuals: top 3 or on advancing team or science top sub-event
    qualified_indiv = []
    for x in indiv_results:
        on_team = any(x[2] in team[-1] for team in qualified_teams)
        top_sub = False
        if subj == 12:
            loc = _location_num(x[4])
            top_sub = (mxbio.get(loc) == x[-3] or mxchem.get(loc) == x[-2] or mxphys.get(loc) == x[-1])
        if x[1] in ['1st', '2nd', '3rd'] or on_team or top_sub:
            qualified_indiv.append(x)

    return qualified_indiv, qualified_teams, wildcard_team

def district_parser(reg_number, params):
    subj = params['subj']
    indiv_results, team_results = [], []
    mxbio, mxchem, mxphys = {}, {}, {}
    empty_districts = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_to_dist = {
            executor.submit(process_single_district, i, params): i
            for i in range(reg_number * 8 - 7, reg_number * 8 + 1)
        }
        for future in concurrent.futures.as_completed(future_to_dist):
            district_num = future_to_dist[future]
            try:
                di, dt, db, dc, dp, de = future.result()
                if di is None:
                    empty_districts.extend(de)
                    continue
                indiv_results.extend(di)
                team_results.extend(dt)
                if subj == 12:
                    _merge_max(mxbio, db)
                    _merge_max(mxchem, dc)
                    _merge_max(mxphys, dp)
            except Exception as e:
                print(f"error in district {district_num}: {e}")
                empty_districts.append(district_num)

    indiv_results.sort(reverse=True)
    team_results.sort(reverse=True)
    all_indiv, all_team = indiv_results[:], team_results[:]

    qualified_indiv, qualified_teams, wildcard = _filter_qualifiers(
        indiv_results, team_results, subj, mxbio, mxchem, mxphys)

    return qualified_indiv, qualified_teams, all_indiv, all_team, empty_districts, wildcard

def regional_parser(params):
    subj, comp, conf = params['subj'], params['comp'], params['conf']
    indiv_results, team_results = [], []
    mxbio, mxchem, mxphys = {}, {}, {}

    for i in range(1, 5):
        try:
            scrape = req(params, reg=str(i))
            regex = f"<tr>.*?{conf}A.*?</tr>"
            indiv_places = re.findall(regex, scrape)

            if not indiv_places:
                indiv_places = re.findall("<tr>.*?HS.*?</tr>", scrape)
            if not indiv_places:
                print(f"nothing in region {i}, continuing...")
                continue

            for x in indiv_places:
                values = re.findall("<td class='ddprint centered'>(.*?)</td>", x)
                place, school, name = values[0], values[1], values[2].strip()
                score = values[-3 if subj % 10 == 1 else -4]
                try: score = int(score)
                except: score = float(score)
                tup = (score, place, name, school, f"Region {i}")

                if subj == 12:
                    bio, chem, phys = int(values[4]), int(values[5]), int(values[6])
                    tup = (score, place, name, school, f"Region {i}", bio, chem, phys)
                    mxbio[i] = max(mxbio.get(i, bio), bio)
                    mxchem[i] = max(mxchem.get(i, chem), chem)
                    mxphys[i] = max(mxphys.get(i, phys), phys)
                indiv_results.append(tup)

            team_places = re.findall("<tr>(.*?)</tr>", scrape)
            for idx in range(len(indiv_places), len(team_places)):
                x = team_places[idx].replace('\u00a0', ' ')
                if x.count("<br>") < 2:
                    continue
                try:
                    place = re.search(r"<td class='ddprint centered'>(.*?)<\/td>", x).group(1).strip()
                    school = re.search(r"<td class='ddprint centered'>(.*?)<span", x).group(1).strip()
                    school = school[school.rindex('>')+1:].strip()
                    score_re = r"<td class='ddprint centered'>(-?\d+)<\/td>"
                    score, prog_score = 0, 0
                    if comp == "CS":
                        score = re.search(score_re * 2, x).group(2)
                        prog_score = re.search(score_re, x).group(1)
                    else:
                        score = re.search(score_re, x).group(1)
                    names = [(N if '<' not in N else N[:N.index('<')]).strip() for N in x.split("<br>")[1:]]
                    if names:
                        team_scores = sorted([t for t in indiv_results if t[2] in names], key=lambda t: t[0], reverse=True)
                        team_results.append((int(score), prog_score if comp == "CS" else -999, -999 if len(team_scores) <= 3 else team_scores[3][0], place, school, f"Region {i}", names))
                except Exception as e:
                    print(f"error in region {i} team row: {e}")
                    continue

        except Exception as e:
            print(f"error in region {i}: {e}")
            continue
        print(f"finished region {i}")

    indiv_results.sort(reverse=True)
    team_results.sort(reverse=True)
    all_indiv, all_team = indiv_results[:], team_results[:]

    qualified_indiv, qualified_teams, wildcard = _filter_qualifiers(
        indiv_results, team_results, subj, mxbio, mxchem, mxphys)

    return qualified_indiv, qualified_teams, all_indiv, all_team, wildcard

@app.route('/')
def index():
    return render_template('index.html', competitions=COMPETITIONS, years=available_years())

@app.route('/results', methods=['POST'])
def get_results():
    data = request.json
    params = {
        'year': int(data['year']) - YEAR_OFFSET,
        'conf': int(data['conf']),
        'subj': int(data['comp']),
        'comp': COMPETITIONS[int(data['comp'])],
    }
    choice = int(data['choice'])
    subj, comp = params['subj'], params['comp']

    wildcard = None
    if choice == 1:
        reg_number = int(data['region'])
        indiv_results, team_results, all_indiv, all_team, empty_districts, wildcard = district_parser(reg_number, params)
    elif choice == 2:
        # parallelize all 4 regions
        indiv_results, team_results, all_indiv, all_team, empty_districts = [], [], [], [], []
        wildcards = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(district_parser, r, params): r for r in range(1, 5)}
            for future in concurrent.futures.as_completed(futures):
                try:
                    I, T, AI, AT, E, W = future.result()
                    indiv_results += I; team_results += T
                    all_indiv += AI; all_team += AT
                    empty_districts += E
                    if W: wildcards.append(W)
                except Exception as e:
                    print(f"error in all-regions: {e}")
        indiv_results.sort(reverse=True)
        team_results.sort(reverse=True)
        all_indiv.sort(reverse=True)
        all_team.sort(reverse=True)
    else:
        indiv_results, team_results, all_indiv, all_team, wildcard = regional_parser(params)
        empty_districts = []

    # format individual results
    formatted_indiv, formatted_all_indiv = [], []
    rank, prev = 1, None
    for res in all_indiv:
        score = int(res[0])
        if score != prev:
            rank = len(formatted_all_indiv) + 1
        prev = score
        is_qualified = res in indiv_results
        d = {"rank": rank, "score": score, "name": res[2], "school": res[3], "district": res[4], "qualified": is_qualified}
        if subj == 12:
            d.update({"bio": res[-3], "chem": res[-2], "phys": res[-1]})
        elif comp == "CS" and len(res) > 5:
            d["prog_score"] = res[5]
        formatted_all_indiv.append(d)
        if is_qualified:
            formatted_indiv.append(d.copy())

    # re-rank qualified individuals
    rank, prev = 1, None
    for r in formatted_indiv:
        if r['score'] != prev:
            rank = sum(1 for x in formatted_indiv if x['score'] > r['score']) + 1
        r['rank'] = rank
        prev = r['score']

    # format team results
    formatted_team, formatted_all_team = [], []
    rank, prev = 1, None
    # figure out which team is the wildcard (for choice==2, pick best wildcard across regions)
    wc = wildcard
    if choice == 2 and wildcards:
        wc = max(wildcards, key=lambda t: t[0])

    for res in all_team:
        score = res[0]
        if score != prev:
            rank = len(formatted_all_team) + 1
        prev = score
        is_qualified = res in team_results
        is_wildcard = (wc is not None and res == wc)
        d = {
            "rank": rank, "score": score, "school": res[4], "district": res[5],
            "names": res[6], "qualified": is_qualified, "wildcard": is_wildcard,
            "fourth": int(res[2]), "prog_score": int(res[1])
        }
        formatted_all_team.append(d)
        if is_qualified:
            formatted_team.append(d.copy())

    # re-rank qualified teams
    rank, prev = 1, None
    for r in formatted_team:
        key = (r['score'], r['prog_score'], r['fourth'])
        if key != prev:
            rank = sum(1 for x in formatted_team if (x['score'], x['prog_score'], x['fourth']) > key) + 1
        r['rank'] = rank
        prev = key

    return jsonify({
        "individual": formatted_indiv,
        "team": formatted_team,
        "all_individual": formatted_all_indiv,
        "all_team": formatted_all_team,
        "empty_districts": empty_districts
    })

@app.route('/download_csv', methods=['POST'])
def download_csv():
    data = request.json
    results, competition = data['results'], data['competition']
    year, conf, choice = data['year'], data['conf'], data['choice']
    type = data['type']
    
    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    if type == 'individual':
        # Write headers for individual results
        if competition == "Science":
            writer.writerow(['Rank', 'Score', 'Name', 'School', 'District', 'Biology', 'Chemistry', 'Physics', 'Qualified?'])
        elif competition == "CS":
            writer.writerow(['Rank', 'Score', 'Name', 'School', 'District', 'Programming Score', 'Qualified?'])
        else:
            writer.writerow(['Rank', 'Score', 'Name', 'School', 'District', 'Qualified?'])
        
        # Write individual results
        for result in results['all_individual']:
            qualification_status = "Yes" if result['qualified'] else ""
            if choice == '3':  # Regional Results
                qualification_status = "Yes" if result['qualified'] else ""
            
            if competition == "Science":
                writer.writerow([
                    result['rank'],
                    result['score'],
                    result['name'],
                    result['school'],
                    result['district'],
                    result['bio'],
                    result['chem'],
                    result['phys'],
                    qualification_status
                ])
            elif competition == "CS":
                writer.writerow([
                    result['rank'],
                    result['score'],
                    result['name'],
                    result['school'],
                    result['district'],
                    result.get('prog_score', ''),
                    qualification_status
                ])
            else:
                writer.writerow([
                    result['rank'],
                    result['score'],
                    result['name'],
                    result['school'],
                    result['district'],
                    qualification_status
                ])
    else:  # team results
        # Write headers for team results
        if competition == "CS":
            writer.writerow(['Rank', 'Score', 'School', 'District', 'Team Members', 'Programming Score', '4th Person Score', 'Qualified?'])
        else:
            writer.writerow(['Rank', 'Score', 'School', 'District', 'Team Members', '4th Person Score', 'Qualified?'])
        
        # Write team results
        for result in results['all_team']:
            qualification_status = "Yes" if result['qualified'] else ""
            if choice == '3':  # Regional Results
                qualification_status = "Yes" if result['qualified'] else ""
            
            if competition == "CS":
                writer.writerow([
                    result['rank'],
                    result['score'],
                    result['school'],
                    result['district'],
                    ', '.join(result['names']),
                    result.get('prog_score', ''),
                    result.get('fourth', ''),
                    qualification_status
                ])
            else:
                writer.writerow([
                    result['rank'],
                    result['score'],
                    result['school'],
                    result['district'],
                    ', '.join(result['names']),
                    result.get('fourth', ''),
                    qualification_status
                ])
    
    # Create the response
    output.seek(0)
    filename = f"{competition}_{year}_{conf}A_{type}_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=filename
    )

if __name__ == '__main__':
    app.run(debug=True) 