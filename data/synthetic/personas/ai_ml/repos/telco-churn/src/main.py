# telco-churn
import shap
explainer = shap.TreeExplainer(model)
from imblearn.over_sampling import SMOTE
from sklearn.ensemble import RandomForestClassifier
