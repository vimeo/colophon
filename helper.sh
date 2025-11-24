



unique_licenses () {
   awk -F',' '{lic=$NF; sub(/\r$/,"",lic); gsub(/^"|"$/,"",lic); gsub(/[()]/,"",lic); gsub(/[[:space:]]*(AND|OR|WITH)[[:space:]]*/,",",lic); n=split(lic,a,/[[:space:]]*,[[:space:]]*/); for(i=1;i<=n;i++){gsub(/^[[:space:]]+|[[:space:]]+$/,"",a[i]); if(a[i]!="") print a[i]}}' ${1} | sort -u
}

unrecognized_licenses () {
   comm -23 <(awk -F',' '{lic=$NF; sub(/\r$/,"",lic); gsub(/^"|"$/,"",lic); gsub(/[()]/,"",lic); gsub(/[[:space:]]*(AND|OR|WITH)[[:space:]]*/,",",lic); n=split(lic,a,/[[:space:]]*,[[:space:]]*/); for(i=1;i<=n;i++){gsub(/^[[:space:]]+|[[:space:]]+$/,"",a[i]); if(a[i]!="") print a[i]}}' ${1} | sort -u) <(cat ${2} | cut -d ',' -f 1 | sort -u)
}

concat_csvs () {
   awk 'FNR > 1' "$@"
}
